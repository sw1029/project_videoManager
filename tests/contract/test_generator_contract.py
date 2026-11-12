"""VideoGenerator 계약 테스트 (Fake + Decorator 조합). 실제 모델 어댑터는 slow/gpu 마커로 같은 스위트에 추가한다.

OpenRouter 어댑터는 가짜 HTTP 전송·가짜 정규화기를 꽂아 네트워크 없이 같은 계약을 확인한다 (실제 호출은 tests/live).
"""

import pytest

from frame.adapters.generator import (
    CachingVideoGenerator,
    FakeVideoAssembler,
    FakeVideoGenerator,
    RetryingVideoGenerator,
)
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import GenerationError
from frame.application.ports.generator import SceneGenerationRequest, VideoGenerator
from frame.application.ports.structurer import ReferenceImageInput
from tests.support.openrouter import PNG_BYTES, make_openrouter_generator


@pytest.fixture
def store():
    s = InMemoryArtifactStore()
    # 요청에 들어 있는 참조 이미지 (실제 생성기는 저장소에서 내용을 읽는다)
    s.put("refs/cup.png", PNG_BYTES, "image/png")
    return s


GENERATORS = [
    pytest.param(lambda store: FakeVideoGenerator(store), id="fake"),
    pytest.param(
        lambda store: CachingVideoGenerator(FakeVideoGenerator(store)), id="caching(fake)"
    ),
    pytest.param(
        lambda store: RetryingVideoGenerator(FakeVideoGenerator(store), retries=2),
        id="retrying(fake)",
    ),
    pytest.param(make_openrouter_generator, id="openrouter(fake-http)"),
]


@pytest.fixture(params=GENERATORS)
def generator(request, store):
    return request.param(store)


def _request(ir, scene_key="intro", seed=7, version_id="ver_1"):
    scene = ir.scene(scene_key)
    return SceneGenerationRequest(
        version_id=version_id,
        scene=scene,
        entities=tuple(ir.entity(k) for k in scene.entity_keys),
        references=(ReferenceImageInput("mem://refs/cup.png", "cup"),),
        seed=seed,
    )


def test_clip_matches_request(generator, store, ir):
    request = _request(ir)
    clip = generator.generate_scene(request)
    assert clip.scene_key == "intro"
    assert clip.duration_ms == request.scene.duration_ms
    assert clip.fingerprint == request.fingerprint
    assert store.exists(clip.uri)


def test_fingerprint_ignores_version_but_tracks_conditions(ir):
    a = _request(ir, version_id="ver_1")
    b = _request(ir, version_id="ver_2")
    assert a.fingerprint == b.fingerprint
    assert _request(ir, seed=8).fingerprint != a.fingerprint
    assert _request(ir, scene_key="closeup").fingerprint != a.fingerprint


def test_assembler_concatenates_in_order(store, ir):
    gen = FakeVideoGenerator(store)
    clips = [gen.generate_scene(_request(ir, k)) for k in ("intro", "closeup", "outro")]
    video = FakeVideoAssembler(store).assemble("ver_1", clips)
    assert video.duration_ms == 12000
    assert store.exists(video.uri)


def test_caching_decorator_generates_once_per_condition(store, ir):
    inner = FakeVideoGenerator(store)
    cached = CachingVideoGenerator(inner)
    first = cached.generate_scene(_request(ir, version_id="ver_1"))
    second = cached.generate_scene(_request(ir, version_id="ver_2"))
    assert first == second and len(inner.requests) == 1 and cached.hits == 1


def test_retrying_decorator_retries_transient_failures(store, ir):
    class Flaky(VideoGenerator):
        def __init__(self, failures):
            self.failures, self.inner = failures, FakeVideoGenerator(store)

        def generate_scene(self, request):
            if self.failures:
                self.failures -= 1
                raise GenerationError("일시 오류")
            return self.inner.generate_scene(request)

    retrying = RetryingVideoGenerator(Flaky(2), retries=2)
    assert retrying.generate_scene(_request(ir)).scene_key == "intro"
    assert retrying.attempts == 3

    exhausted = RetryingVideoGenerator(Flaky(3), retries=2)
    with pytest.raises(GenerationError):
        exhausted.generate_scene(_request(ir))


def test_retrying_decorator_does_not_retry_non_retryable_failures(store, ir):
    """외부 API 가 이미 작업을 받은 뒤의 실패는 다시 제출하면 또 과금되므로 retryable=False 로 보고된다."""

    class Billed(VideoGenerator):
        calls = 0

        def generate_scene(self, request):
            self.calls += 1
            raise GenerationError("작업 실패 (이미 과금됐을 수 있음)", retryable=False)

    inner = Billed()
    retrying = RetryingVideoGenerator(inner, retries=3)
    with pytest.raises(GenerationError) as excinfo:
        retrying.generate_scene(_request(ir))
    assert excinfo.value.retryable is False
    assert inner.calls == 1 and retrying.attempts == 1


def test_generation_errors_are_retryable_by_default():
    assert GenerationError("일시 오류").retryable is True
    assert GenerationError().args == ()  # 기존 생성 방식 그대로
