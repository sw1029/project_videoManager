"""생성 시간 초과 (이슈 #1).

시간을 넘긴 버전은 FAILED(GENERATION_TIMEOUT) 로 기록되고 후속 단계(추적·검수)는 실행되지 않는다.
시간 초과 장면은 다시 제출하지 않으며, 수정 버전이 시간을 넘겨도 부모(원본) 버전은 변하지 않는다.
"""

import threading

import pytest

from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.adapters.tracker import FakeObjectTracker
from frame.application.dto import CreateRevisionCommand
from frame.application.ports.generator import VideoGenerator
from frame.config import Settings
from frame.domain.enums import VersionState
from frame.domain.revision import AttributeChange
from tests.support.factories import create_draft, generate, make_container, version_of

# 느린 장면만 마감을 넘기고, 나머지 장면(Fake, 1ms 미만)은 느린 CI 에서도 넉넉히 끝나는 값
TIMEOUT_S = 0.5


class SlowSceneGenerator(VideoGenerator):
    """slow_scene_keys 장면은 release 될 때까지 끝나지 않는다 (실제 모델의 긴 생성 시간을 흉내)."""

    def __init__(self, store, release: threading.Event, slow_scene_keys) -> None:
        self.inner = FakeVideoGenerator(store)
        self.release = release
        self.slow = set(slow_scene_keys)
        self.calls: list[str] = []

    def generate_scene(self, request):
        self.calls.append(request.scene.scene_key)
        if request.scene.scene_key in self.slow:
            self.release.wait(5)
        return self.inner.generate_scene(request)


@pytest.fixture
def release():
    event = threading.Event()
    yield event
    event.set()  # 버려진 작업 스레드를 남기지 않는다


def _settings(**overrides) -> Settings:
    # 캐시는 끈다: 시간 제한 데코레이터가 체인 맨 바깥(container.generator)에 오고, 캐시 기본값이 바뀌어도 흔들리지 않게
    return Settings(
        jobs="recording",
        persistence="memory",
        generator_timeout_s=TIMEOUT_S,
        generator_cache=False,
        **overrides,
    )


def test_generation_timeout_fails_the_version_and_skips_later_stages(release):
    store = InMemoryArtifactStore()
    slow = SlowSceneGenerator(store, release, {"closeup"})
    tracker = FakeObjectTracker()
    container = make_container(
        _settings(generator_retries=2), artifact_store=store, generator=slow, tracker=tracker
    )
    draft = create_draft(container)

    version = generate(container, draft.version_id)

    assert version.state is VersionState.FAILED
    assert version.error_code == "GENERATION_TIMEOUT"
    assert version.error_message.startswith("[generate] 장면 'closeup' 생성이 0.5초 안에")
    assert version.video_uri is None
    assert tracker.requests == []  # 추적 미실행
    assert container.queries.get_checks(draft.version_id).results == ()  # 검수 미실행

    release.set()
    assert container.generator.wait_abandoned(5)
    # retries=2 여도 시간 초과 장면을 다시 제출하지 않았다
    assert slow.calls == ["intro", "closeup"]


def test_failed_message_reports_how_long_each_stage_took(release):
    store = InMemoryArtifactStore()
    container = make_container(
        _settings(),
        artifact_store=store,
        generator=SlowSceneGenerator(store, release, {"intro"}),
    )
    draft = create_draft(container)

    version = generate(container, draft.version_id)

    assert "· 소요: generate " in version.error_message


def test_revision_timeout_leaves_the_parent_version_untouched(release):
    store = InMemoryArtifactStore()
    container = make_container(artifact_store=store)
    draft = create_draft(container)
    original = generate(container, draft.version_id)
    assert original.state is VersionState.COMPLETED

    # 수정 버전만 시간을 넘기도록 생성기를 바꾼 컨테이너 (같은 저장소·영속성 공유)
    slow_container = make_container(
        _settings(),
        artifact_store=store,
        generator=SlowSceneGenerator(store, release, {"intro"}),
        uow_factory=container.uow_factory,
        ids=container.ids,
        clock=container.clock,
    )
    revision = slow_container.create_revision.execute(
        CreateRevisionCommand(draft.version_id, AttributeChange("cup", "color", "teal"))
    )
    revised = generate(slow_container, revision.version_id)

    assert (revised.state, revised.error_code) == (VersionState.FAILED, "GENERATION_TIMEOUT")
    assert version_of(container, draft.version_id) == original
    assert store.exists(original.video_uri)
