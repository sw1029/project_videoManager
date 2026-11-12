"""워커 파이프라인: 생성 -> 추적 -> 검수. 성공은 COMPLETED, 실행 오류는 FAILED + 원인, 원본은 보존."""

import pytest

from frame.adapters.checker import FakeRequirementChecker
from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.adapters.tracker import FakeObjectTracker
from frame.application.dto import CreateRevisionCommand
from frame.application.errors import TrackingError
from frame.application.ports.checker import Checker
from frame.application.ports.generator import VideoGenerator
from frame.domain.enums import CheckType, TrackingStatus, Verdict, VersionState
from frame.domain.errors import InvalidStateTransition
from frame.domain.services.propagation import AttributeChange
from tests.support.clock import ManualClock
from tests.support.factories import (
    create_draft,
    dispatcher_of,
    generate,
    make_container,
    version_of,
)


def test_pipeline_completes_and_persists_video_tracks_and_checks(container, draft):
    version = generate(container, draft.version_id)

    assert version.state is VersionState.COMPLETED
    assert version.video_uri and container.artifact_store.exists(version.video_uri)
    assert set(version.clips) == {"intro", "closeup", "outro"}

    tracks = container.queries.get_entity_tracks(draft.version_id, "cup").records
    assert [(t.scene_key, t.status) for t in tracks] == [
        ("intro", TrackingStatus.COMPLETED),
        ("closeup", TrackingStatus.COMPLETED),
    ]

    checks = container.queries.get_checks(draft.version_id)
    by_type = {t: [r for r in checks.results if r.check_type is t] for t in CheckType}
    assert len(by_type[CheckType.REQUIREMENT]) == 8
    assert all(r.verdict is Verdict.PASS for r in by_type[CheckType.REQUIREMENT])
    assert {r.entity_key for r in by_type[CheckType.CONSISTENCY]} == {"barista", "cup", "cafe"}
    assert by_type[CheckType.PRESERVATION] == []  # 원본 버전에는 보존 검수가 없다
    assert checks.summary.requirement_reflection_rate == 1.0


def test_generation_failure_marks_version_failed_with_cause():
    store = InMemoryArtifactStore()
    container = make_container(
        artifact_store=store, generator=FakeVideoGenerator(store, fail_scene_keys={"closeup"})
    )
    draft = create_draft(container)
    version = generate(container, draft.version_id)

    assert version.state is VersionState.FAILED
    assert version.error_code == "GENERATION_ERROR"
    assert version.error_message.startswith("[generate]")
    assert version.video_uri is None
    assert container.queries.get_checks(draft.version_id).results == ()


def test_tracking_error_aborts_following_stages():
    container = make_container(tracker=FakeObjectTracker(error=TrackingError("추적기 로드 실패")))
    draft = create_draft(container)
    version = generate(container, draft.version_id)
    assert (version.state, version.error_code) == (VersionState.FAILED, "TRACKING_ERROR")
    assert "[track]" in version.error_message


def test_unexpected_checker_exception_is_recorded_not_raised():
    class ExplodingChecker(Checker):
        check_type = CheckType.REQUIREMENT

        def check(self, ctx):
            raise RuntimeError("boom")

    container = make_container(checkers=(ExplodingChecker(),))
    draft = create_draft(container)
    version = generate(container, draft.version_id)
    assert (version.state, version.error_code) == (VersionState.FAILED, "UNEXPECTED_ERROR")
    assert "RuntimeError: boom" in version.error_message


def test_failed_tracking_of_an_entity_makes_its_requirements_undetermined():
    container = make_container(tracker=FakeObjectTracker(fail_entity_keys={"cup"}))
    draft = create_draft(container)
    version = generate(container, draft.version_id)

    assert version.state is VersionState.COMPLETED  # 미판정은 실행 오류가 아니다
    results = container.queries.get_checks(draft.version_id).results
    cup_reqs = [
        r for r in results if r.check_type is CheckType.REQUIREMENT and r.entity_key == "cup"
    ]
    assert cup_reqs and all(r.verdict is Verdict.UNDETERMINED for r in cup_reqs)
    assert (
        container.queries.get_checks(draft.version_id).summary.requirement_reflection_rate == 6 / 8
    )


def test_run_requires_queued_state(container, draft):
    with pytest.raises(InvalidStateTransition):
        container.run_generation_pipeline.execute(draft.version_id)


@pytest.mark.ac("AC-01")
def test_revision_regenerates_only_changed_scenes_and_reuses_the_rest():
    store = InMemoryArtifactStore()
    generator = FakeVideoGenerator(store)
    container = make_container(artifact_store=store, generator=generator)
    draft = create_draft(container)
    original = generate(container, draft.version_id)
    generator.requests.clear()

    revision = container.create_revision.execute(
        CreateRevisionCommand(draft.version_id, AttributeChange("cup", "color", "teal"))
    )
    revised = generate(container, revision.version_id)

    assert revised.state is VersionState.COMPLETED
    assert [r.scene.scene_key for r in generator.requests] == [
        "intro",
        "closeup",
    ]  # outro 는 재생성 안 함
    assert revised.clips["outro"].uri == original.clips["outro"].uri  # 비대상 장면은 바이트 동일
    assert revised.clips["intro"].uri != original.clips["intro"].uri
    assert revised.video_uri != original.video_uri

    preservation = [
        r
        for r in container.queries.get_checks(revision.version_id).results
        if r.check_type is CheckType.PRESERVATION
    ]
    assert {r.scene_key: r.detail["reason"] for r in preservation} == {
        "intro": "regenerated",
        "closeup": "regenerated",
        "outro": "clip_reused",
    }
    assert container.queries.get_checks(revision.version_id).summary.preservation_rate >= 0.9


def test_revision_failure_keeps_original_intact():
    store = InMemoryArtifactStore()
    container = make_container(artifact_store=store, generator=FakeVideoGenerator(store))
    draft = create_draft(container)
    original = generate(container, draft.version_id)

    # 수정 버전만 실패하도록 생성기를 교체한 새 컨테이너 (같은 저장소 공유)
    failing = make_container(
        artifact_store=store,
        generator=FakeVideoGenerator(store, fail_scene_keys={"intro"}),
        uow_factory=container.uow_factory,
        ids=container.ids,
        clock=container.clock,
    )
    revision = failing.create_revision.execute(
        CreateRevisionCommand(draft.version_id, AttributeChange("cup", "color", "teal"))
    )
    revised = generate(failing, revision.version_id)

    assert revised.state is VersionState.FAILED
    assert version_of(container, draft.version_id) == original


def test_requirement_checker_fail_still_completes_version():
    container = make_container(checkers=(FakeRequirementChecker(failing_keys={"intro:cup:color"}),))
    draft = create_draft(container)
    version = generate(container, draft.version_id)
    assert version.state is VersionState.COMPLETED
    summary = container.queries.get_checks(draft.version_id).summary
    assert summary.failed == 1 and summary.requirement_reflection_rate == 7 / 8


def test_run_records_the_start_time_and_counts_the_attempt():
    """#2: QUEUED -> PROCESSING 때 시작 시각을 남기고 시도 횟수를 1 늘린다."""
    clock = ManualClock()
    container = make_container(clock=clock)
    draft = create_draft(container)
    container.accept_generation.execute(draft.version_id)
    queued_at = clock.current
    started_at = clock.advance(seconds=30)

    dispatcher_of(container).run_all()

    version = version_of(container, draft.version_id)
    assert version.state is VersionState.COMPLETED
    assert (version.queued_at, version.started_at, version.attempt) == (queued_at, started_at, 1)


class _ProcessDied(BaseException):
    """작업 도중 프로세스가 죽었다 (어떤 처리기도 잡지 않는다)."""


class _DyingGenerator(VideoGenerator):
    def generate_scene(self, request):
        raise _ProcessDied


def test_start_is_committed_before_the_stages_run():
    """생성 도중 프로세스가 죽어도 '언제 몇 번째로 시작했는지' 는 남아 복구가 판단할 수 있다."""
    clock = ManualClock()
    container = make_container(clock=clock, generator=_DyingGenerator())
    draft = create_draft(container)
    container.accept_generation.execute(draft.version_id)

    with pytest.raises(_ProcessDied):
        dispatcher_of(container).run_all()

    version = version_of(container, draft.version_id)
    assert (version.state, version.started_at, version.attempt) == (
        VersionState.PROCESSING,
        clock.current,
        1,
    )
