"""VideoGenerator 계약 테스트 (Fake + Decorator 조합). 실제 모델 어댑터는 slow/gpu 마커로 같은 스위트에 추가한다.

OpenRouter 어댑터는 가짜 HTTP 전송·가짜 정규화기를 꽂아 네트워크 없이 같은 계약을 확인한다 (실제 호출은 tests/live).
"""

from dataclasses import replace

import pytest

from frame.adapters.generator import (
    CachingVideoGenerator,
    FakeVideoAssembler,
    FakeVideoGenerator,
    RetryingVideoGenerator,
    TimeoutVideoGenerator,
)
from frame.adapters.generator.cache import ArtifactClipCache
from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
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
        lambda store: CachingVideoGenerator(
            FakeVideoGenerator(store), cache=ArtifactClipCache(store, {"generator": "fake"})
        ),
        id="caching(fake, artifact-store)",
    ),
    pytest.param(
        lambda store: RetryingVideoGenerator(FakeVideoGenerator(store), retries=2),
        id="retrying(fake)",
    ),
    pytest.param(
        lambda store: TimeoutVideoGenerator(FakeVideoGenerator(store), timeout_seconds=30),
        id="timeout(fake)",
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
    assert len(inner.requests) == 1 and cached.hits == 1
    assert (second.uri, second.fingerprint) == (first.uri, first.fingerprint)
    assert second.metadata["cache_hit"] is True  # 적중은 복사본의 메타데이터에 남는다


def test_persistent_cache_does_not_regenerate_after_restart(tmp_path, ir):
    """#7: 같은 생성 조건은 프로세스를 다시 시작해도 한 번만 생성된다 (같은 산출물 폴더, 새 인스턴스)."""
    profile = {"generator": "fake"}
    before_store = LocalFileArtifactStore(tmp_path)
    before_inner = FakeVideoGenerator(before_store)
    before = CachingVideoGenerator(before_inner, cache=ArtifactClipCache(before_store, profile))
    first = before.generate_scene(_request(ir, version_id="ver_1"))

    after_store = LocalFileArtifactStore(tmp_path)  # 재시작: 메모리에 남은 것이 없다
    after_inner = FakeVideoGenerator(after_store)
    after = CachingVideoGenerator(after_inner, cache=ArtifactClipCache(after_store, profile))
    second = after.generate_scene(_request(ir, version_id="ver_2"))

    assert len(before_inner.requests) == 1 and after_inner.requests == []
    assert (second.uri, second.fingerprint, second.duration_ms) == (
        first.uri,
        first.fingerprint,
        first.duration_ms,
    )
    assert after_store.exists(second.uri) and second.metadata["cache_hit"] is True
    # 조건이 다른 장면은 그대로 생성한다
    after.generate_scene(_request(ir, scene_key="closeup", version_id="ver_2"))
    assert len(after_inner.requests) == 1


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


# ---- 정체성 기준 이미지 (ADR-0015) ----------------------------------------------------


def _with_identity(request, *uris):
    return replace(
        request, identity_references=tuple(ReferenceImageInput(uri, "barista") for uri in uris)
    )


def test_identity_references_change_the_generation_key_but_not_the_conditions(ir):
    plain = _request(ir)
    anchored = _with_identity(plain, "mem://refs/cup.png")

    assert plain.generation_fingerprint == plain.fingerprint  # 기준이 없으면 예전 키 그대로
    assert anchored.fingerprint == plain.fingerprint  # 버전 사이 재사용 판단은 그대로다
    assert anchored.generation_fingerprint != plain.fingerprint
    other = _with_identity(plain, "mem://refs/other.png")
    assert other.generation_fingerprint != anchored.generation_fingerprint
    described = replace(
        plain,
        identity_references=(ReferenceImageInput("mem://refs/cup.png", "barista", "짧은 머리"),),
    )
    assert described.generation_fingerprint != anchored.generation_fingerprint


def test_clip_fingerprint_stays_the_condition_fingerprint_with_identity_references(
    generator, store, ir
):
    request = _with_identity(_request(ir), "mem://refs/cup.png")
    clip = generator.generate_scene(request)

    assert clip.fingerprint == request.fingerprint
    assert generator.result_key(request) == request.generation_fingerprint
    assert generator.result_key(_request(ir)) == _request(ir).fingerprint


def test_cache_keeps_results_with_different_identity_references_apart(store, ir):
    inner = FakeVideoGenerator(store)
    cached = CachingVideoGenerator(inner)
    plain = _request(ir)
    anchored = _with_identity(plain, "mem://refs/cup.png")

    cached.generate_scene(plain)
    cached.generate_scene(anchored)  # 같은 조건이지만 기준 이미지가 생겼다: 따로 생성한다
    cached.generate_scene(_with_identity(_request(ir, version_id="ver_2"), "mem://refs/cup.png"))

    assert len(inner.requests) == 2 and cached.hits == 1
