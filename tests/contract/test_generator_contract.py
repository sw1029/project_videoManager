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
from frame.application.ports.generator import (
    GeneratedClip,
    ParentClip,
    ProductionMethod,
    ProductionPreview,
    SceneGenerationRequest,
    VideoGenerator,
)
from frame.application.ports.structurer import ReferenceImageInput
from frame.domain.services.propagation import AttributeChange, DefaultPropagationPolicy
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


# ---- 부모 클립 (ADR-0016) ------------------------------------------------------------


def _with_parent(request, ir, store, uri="mem://clips/ver_0/intro.mp4"):
    """부모 버전에서 같은 장면을 빨간 컵으로 만든 클립을 실은 요청 (지금 요청은 컵 색만 다르다)."""
    parent_scene = ir.scene(request.scene.scene_key)
    clip = GeneratedClip(
        parent_scene.scene_key,
        uri,
        parent_scene.duration_ms,
        "parent-fingerprint",
        {"generator": "fake", "job_id": "gen-vid-1789480874-Ab3dEf9hIjKlMnOpQrSt"},
    )
    parent = ParentClip(
        "ver_0", clip, parent_scene, tuple(ir.entity(k) for k in parent_scene.entity_keys)
    )
    return replace(request, parent=parent)


def _recolored(ir):
    return DefaultPropagationPolicy().apply(ir, AttributeChange("cup", "color", "teal")).ir


def test_parent_clip_is_not_part_of_the_condition_fingerprint(ir, store):
    request = _request(_recolored(ir))
    with_parent = _with_parent(request, ir, store)

    assert with_parent.fingerprint == request.fingerprint
    assert with_parent.generation_fingerprint == request.generation_fingerprint


def test_request_with_a_parent_clip_keeps_the_generator_contract(generator, store, ir):
    """부모 클립을 편집하든 무시하고 새로 만들든, 돌려주는 클립의 계약은 같다."""
    request = _with_parent(_request(_recolored(ir)), ir, store)

    clip = generator.generate_scene(request)

    assert clip.scene_key == "intro"
    assert clip.duration_ms == request.scene.duration_ms
    assert clip.fingerprint == request.fingerprint
    assert store.exists(clip.uri)


def test_generators_that_do_not_edit_ignore_the_parent_in_their_result_key(generator, store, ir):
    request = _request(_recolored(ir))
    assert generator.result_key(_with_parent(request, ir, store)) == generator.result_key(request)


def test_fake_generator_records_the_parent_clip(store, ir):
    generator = FakeVideoGenerator(store)
    request = _with_parent(_request(_recolored(ir)), ir, store)

    clip = generator.generate_scene(request)

    assert generator.requests[-1].parent.clip.uri == "mem://clips/ver_0/intro.mp4"
    assert clip.metadata["parent_clip_uri"] == "mem://clips/ver_0/intro.mp4"
    assert clip.metadata["edited_parent"] is False  # 기록만 하고 새로 만든다
    assert "parent_clip_uri" not in generator.generate_scene(_request(ir)).metadata


def test_editing_fake_generator_keys_its_result_on_the_parent_clip(store, ir):
    generator = FakeVideoGenerator(store, edits_parent=True)
    request = _request(_recolored(ir))
    from_a = _with_parent(request, ir, store, "mem://clips/ver_a/intro.mp4")
    from_b = _with_parent(request, ir, store, "mem://clips/ver_b/intro.mp4")

    assert generator.result_key(request) == request.generation_fingerprint
    assert generator.result_key(from_a) != request.generation_fingerprint
    assert generator.result_key(from_a) != generator.result_key(from_b)
    assert generator.generate_scene(from_a).metadata["edited_parent"] is True


def test_cache_follows_the_result_key_of_an_editing_generator(store, ir):
    inner = FakeVideoGenerator(store, edits_parent=True)
    cached = CachingVideoGenerator(inner)
    request = _request(_recolored(ir))

    cached.generate_scene(_with_parent(request, ir, store, "mem://clips/ver_a/intro.mp4"))
    cached.generate_scene(_with_parent(request, ir, store, "mem://clips/ver_b/intro.mp4"))
    cached.generate_scene(_with_parent(request, ir, store, "mem://clips/ver_a/intro.mp4"))

    assert len(inner.requests) == 2 and cached.hits == 1  # 부모가 다르면 편집 결과도 다르다


# ---- 만들기 전 미리보기 (ADR-0020) -------------------------------------------------------


def test_preview_tells_how_a_scene_would_be_made_without_making_it(generator, store, ir):
    """변경 계획 미리보기가 쓴다: 방식과 어림값만 돌려주고 아무것도 만들지 않는다 (과금 없음). 모르면 None."""
    request = _with_parent(_request(_recolored(ir)), ir, store)
    before = set(store._blobs)

    for asked in (request, replace(request, parent=None)):
        preview = generator.preview(asked)
        assert preview is None or isinstance(preview, ProductionPreview)
        if preview is not None:
            assert preview.method in (ProductionMethod.GENERATE, ProductionMethod.EDIT)
            # 편집할 수 없는 사유는 새로 생성으로 바뀔 때만 있다
            assert preview.edit_unavailable_reason is None or (
                preview.method is ProductionMethod.GENERATE
            )

    assert set(store._blobs) == before  # 클립도 작업 기록도 생기지 않았다


def test_generators_that_cannot_tell_have_no_preview(store, ir):
    class Plain(VideoGenerator):
        def generate_scene(self, request):
            raise AssertionError("미리보기는 생성하지 않는다")

    assert Plain().preview(_request(ir)) is None


def test_fake_generator_previews_generation_or_an_edit_of_the_parent(store, ir):
    request = _request(_recolored(ir))
    with_parent = _with_parent(request, ir, store)
    plain, editing = FakeVideoGenerator(store), FakeVideoGenerator(store, edits_parent=True)

    assert plain.preview(with_parent) == ProductionPreview(ProductionMethod.GENERATE)
    assert editing.preview(with_parent) == ProductionPreview(ProductionMethod.EDIT)
    assert editing.preview(request) == ProductionPreview(
        ProductionMethod.GENERATE
    )  # 부모 클립이 없다
    assert editing.previews == [with_parent, request] and not editing.requests


DECORATORS = pytest.mark.parametrize(
    "wrap",
    [
        lambda inner: CachingVideoGenerator(inner),
        lambda inner: RetryingVideoGenerator(inner, retries=2),
        lambda inner: TimeoutVideoGenerator(inner, timeout_seconds=30),
    ],
    ids=["caching", "retrying", "timeout"],
)


@DECORATORS
def test_decorators_pass_the_preview_through(wrap, store, ir):
    inner = FakeVideoGenerator(store, edits_parent=True)
    request = _with_parent(_request(_recolored(ir)), ir, store)

    assert wrap(inner).preview(request) == ProductionPreview(ProductionMethod.EDIT)
    assert inner.previews == [request] and not inner.requests


@DECORATORS
def test_decorators_pass_the_result_key_through(wrap, store, ir):
    """편집하는 생성기의 결과 키에는 부모 클립이 들어 있다. 데코레이터가 포트의 기본값(조건만)으로 답하면
    부모 클립이 다른 편집 결과가 같은 키로 캐시된다 (감사 T-06).
    """
    inner = FakeVideoGenerator(store, edits_parent=True)
    request = _with_parent(_request(_recolored(ir)), ir, store)

    assert wrap(inner).result_key(request) == inner.result_key(request)
    assert inner.result_key(request) != request.generation_fingerprint  # 기본값과 다른 값이다


@DECORATORS
def test_decorators_pass_the_profile_through(wrap, store):
    class Profiled(FakeVideoGenerator):
        def profile(self):
            return {"generator": "fake", "model": "sketch-2", "fps": 12}

    assert wrap(Profiled(store)).profile() == {"generator": "fake", "model": "sketch-2", "fps": 12}


def test_a_cache_around_the_whole_chain_keeps_edits_of_different_parents_apart(store, ir):
    """컨테이너가 조립하는 순서 (캐시 > 시간 제한 > 재시도 > 생성기). 가운데 데코레이터 하나라도 결과 키를
    넘기지 않으면, 부모 클립이 다른 두 편집이 같은 캐시 항목이 된다.
    """
    inner = FakeVideoGenerator(store, edits_parent=True)
    chain = CachingVideoGenerator(
        TimeoutVideoGenerator(RetryingVideoGenerator(inner, retries=1), timeout_seconds=30)
    )
    request = _request(_recolored(ir))
    from_a = _with_parent(request, ir, store, "mem://clips/ver_a/intro.mp4")
    from_b = _with_parent(request, ir, store, "mem://clips/ver_b/intro.mp4")

    chain.generate_scene(from_a)
    chain.generate_scene(from_b)
    chain.generate_scene(from_a)

    assert len(inner.requests) == 2 and chain.hits == 1
