"""실패 시 부분 결과 보존과 처리 중 진행 저장 (이슈 #3).

FAILED 버전에서도 어느 단계까지 무엇이 만들어졌는지 조회할 수 있어야 한다.
- 생성 단계에서 장면 일부만 끝나고 실패해도 끝난 장면 클립은 버전에 남는다 (과금된 결과를 잃지 않는다).
- 추적 단계에서 실패하면 클립·조립 영상·끝난 추적 결과는 남고 검수 결과는 없다.
- 진행 상황(단계·장면)은 처리 도중에도 저장되어 thread 디스패처에서 GET 으로 보인다.
"""

import pytest

from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.adapters.tracker import FakeObjectTracker
from frame.application.dto import CreateRevisionCommand
from frame.application.errors import TrackingError
from frame.application.ports.checker import Checker, CheckOutcome
from frame.application.ports.generator import VideoGenerator
from frame.application.ports.tracker import ObjectTracker
from frame.domain.enums import ChangeScope, CheckType, TrackingStatus, Verdict, VersionState
from frame.domain.errors import MediaNotAvailable
from frame.domain.progress import StageStatus
from frame.domain.revision import AttributeChange
from frame.domain.tracking import TrackRecord
from tests.support.factories import create_draft, generate, make_container

RUNNING, COMPLETED, FAILED = StageStatus.RUNNING, StageStatus.COMPLETED, StageStatus.FAILED


def _stages(version):
    return [(s.stage, s.status, s.done, s.total, s.current) for s in version.progress.stages]


class FailsOnScene(ObjectTracker):
    """지정한 장면에서만 추적 자체가 실패한다 (앞 장면 추적은 끝난 뒤)."""

    def __init__(self, scene_key: str) -> None:
        self.inner, self.scene_key = FakeObjectTracker(), scene_key

    def track(self, request):
        if request.scene.scene_key == self.scene_key:
            raise TrackingError(f"장면 {self.scene_key} 추적 실패")
        return self.inner.track(request)


def test_generation_failure_on_the_second_scene_keeps_the_first_clip():
    store = InMemoryArtifactStore()
    container = make_container(
        artifact_store=store, generator=FakeVideoGenerator(store, fail_scene_keys={"closeup"})
    )
    vid = create_draft(container).version_id

    version = generate(container, vid)

    assert (version.state, version.error_code) == (VersionState.FAILED, "GENERATION_ERROR")
    assert set(version.clips) == {"intro"}
    assert store.exists(version.clips["intro"].uri)
    assert container.queries.get_clip_uri(vid, "intro") == version.clips["intro"].uri
    with pytest.raises(MediaNotAvailable):
        container.queries.get_clip_uri(vid, "closeup")
    assert version.video_uri is None  # 조립 전에 실패
    assert _stages(version) == [("generate", FAILED, 1, 3, "closeup")]
    assert version.error_message.startswith("[generate] 장면 'closeup' 생성 실패")
    assert " · 진행: 장면 closeup (2/3) 에서 실패 · 소요: generate " in version.error_message


def test_tracking_failure_keeps_clips_and_video_but_has_no_check_results():
    container = make_container(tracker=FakeObjectTracker(error=TrackingError("추적기 로드 실패")))
    vid = create_draft(container).version_id

    version = generate(container, vid)

    assert (version.state, version.error_code) == (VersionState.FAILED, "TRACKING_ERROR")
    assert set(version.clips) == {"intro", "closeup", "outro"}
    assert version.video_uri and container.artifact_store.exists(version.video_uri)
    assert container.queries.get_video_uri(vid) == version.video_uri
    assert container.queries.get_checks(vid).results == ()
    assert _stages(version) == [
        ("generate", COMPLETED, 3, 3, None),
        ("track", FAILED, 0, 3, "intro"),
    ]
    assert version.error_message.startswith("[track] 추적기 로드 실패 · 진행: 장면 intro (1/3)")


def test_tracks_finished_before_a_tracking_failure_are_kept():
    container = make_container(tracker=FailsOnScene("closeup"))
    vid = create_draft(container).version_id

    version = generate(container, vid)

    assert version.error_code == "TRACKING_ERROR"
    cup = container.queries.get_entity_tracks(vid, "cup").records
    assert [(t.scene_key, t.status) for t in cup] == [
        ("intro", TrackingStatus.COMPLETED),
        ("closeup", TrackingStatus.PENDING),
    ]
    assert _stages(version)[-1] == ("track", FAILED, 1, 3, "closeup")


def test_version_is_still_marked_failed_when_partial_results_cannot_be_saved(caplog):
    """부분 결과 저장이 무결성 오류로 실패해도 버전이 PROCESSING 에 묶이지 않고 FAILED 로 남는다."""

    class BadThenBroken(ObjectTracker):
        def track(self, request):
            if request.scene.scene_key == "intro":
                # 장면에 없는 객체의 추적 결과: 저장할 때 무결성 위반이 난다
                return (TrackRecord(request.version_id, "intro", "ghost"),)
            raise TrackingError("추적기 중단")

    container = make_container(tracker=BadThenBroken())
    vid = create_draft(container).version_id

    version = generate(container, vid)

    assert (version.state, version.error_code) == (VersionState.FAILED, "TRACKING_ERROR")
    assert "부분 결과를 저장하지 못해" in caplog.text


def test_check_failure_keeps_everything_produced_before_it():
    class WrongRequirement(Checker):
        """이 버전에 없는 요구조건의 결과를 돌려준다. 검수 단계가 무결성 위반으로 실패한다.

        (검수기가 예외로 끝나는 것은 단계의 실패가 아니다: 그 판정만 미판정으로 남는다. 감사 A-13)
        """

        check_type = CheckType.REQUIREMENT

        def check(self, ctx):
            return [CheckOutcome(self.check_type, Verdict.PASS, requirement_key="ghost.color")]

    container = make_container(checkers=(WrongRequirement(),))
    vid = create_draft(container).version_id

    version = generate(container, vid)

    assert (version.state, version.error_code) == (VersionState.FAILED, "INTEGRITY_VIOLATION")
    assert version.video_uri and len(version.clips) == 3
    assert [t.status for t in container.queries.get_entity_tracks(vid, "cup").records] == [
        TrackingStatus.COMPLETED,
        TrackingStatus.COMPLETED,
    ]
    assert container.queries.get_checks(vid).results == ()
    assert _stages(version)[-1] == ("check", FAILED, 0, 0, None)
    assert " · 진행: " not in version.error_message  # 항목 단위 진행이 없는 단계


def test_completed_version_records_every_stage(container, draft):
    version = generate(container, draft.version_id)

    assert _stages(version) == [
        ("generate", COMPLETED, 3, 3, None),
        ("track", COMPLETED, 3, 3, None),
        ("check", COMPLETED, 0, 0, None),
    ]
    assert all(s.elapsed_ms >= 0 for s in version.progress.stages)


def test_versions_that_never_ran_have_no_progress(container, draft):
    assert container.queries.get_version(draft.version_id).version.progress is None


def test_progress_and_finished_clips_are_saved_while_the_pipeline_runs():
    """thread 디스패처에서 처리 중인 버전을 GET 하는 상황: 장면을 만들기 직전에 저장된 버전을 읽어 본다."""

    class Probe(VideoGenerator):
        def __init__(self, inner):
            self.inner, self.queries, self.seen = inner, None, {}

        def generate_scene(self, request):
            stored = self.queries.get_version(request.version_id).version
            self.seen[request.scene.scene_key] = (
                stored.state,
                stored.progress.current,
                set(stored.clips),
            )
            return self.inner.generate_scene(request)

    store = InMemoryArtifactStore()
    probe = Probe(FakeVideoGenerator(store))
    container = make_container(artifact_store=store, generator=probe)
    probe.queries = container.queries
    vid = create_draft(container).version_id

    generate(container, vid)

    state, progress, clips = probe.seen["outro"]
    assert state is VersionState.PROCESSING
    assert (progress.stage, progress.status, progress.done, progress.total, progress.current) == (
        "generate",
        RUNNING,
        2,
        3,
        "outro",
    )
    assert clips == {"intro", "closeup"}  # 끝난 장면 클립도 처리 중에 보인다
    assert probe.seen["intro"][2] == set()


def test_revision_of_a_failed_version_reuses_its_finished_scenes():
    """실패한 버전에 남은 장면 클립은 수정 버전에서 재사용된다 (같은 조건이면 다시 과금하지 않는다)."""
    store = InMemoryArtifactStore()
    failing = make_container(
        artifact_store=store, generator=FakeVideoGenerator(store, fail_scene_keys={"closeup"})
    )
    vid = create_draft(failing).version_id
    failed = generate(failing, vid)
    assert set(failed.clips) == {"intro"}

    generator = FakeVideoGenerator(store)
    working = make_container(
        artifact_store=store,
        generator=generator,
        uow_factory=failing.uow_factory,
        ids=failing.ids,
        clock=failing.clock,
    )
    change = AttributeChange(
        "cup", "color", "teal", scope=ChangeScope.SCENES, scene_keys=("closeup",)
    )
    revision = working.create_revision.execute(CreateRevisionCommand(vid, change))
    revised = generate(working, revision.version_id)

    assert revised.state is VersionState.COMPLETED
    assert [r.scene.scene_key for r in generator.requests] == ["closeup", "outro"]
    assert revised.clips["intro"].uri == failed.clips["intro"].uri
