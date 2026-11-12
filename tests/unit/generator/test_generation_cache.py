"""생성 결과 캐시 (#7, ADR-0013): (fingerprint, 생성기 프로필) -> 장면 클립.

ArtifactStore 에 영속하므로 재시작 뒤에도 같은 조건은 다시 만들지 않는다 (재시작 계약은 tests/contract 에 있다).
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from frame.adapters.generator import (
    CachingVideoGenerator,
    FakeVideoGenerator,
    RetryingVideoGenerator,
)
from frame.adapters.generator.cache import (
    ArtifactClipCache,
    InMemoryClipCache,
    build_clip_cache,
    generation_key,
)
from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
from frame.application.errors import GenerationError, StorageError
from frame.application.ports.generator import SceneGenerationRequest
from tests.support.openrouter import make_openrouter_generator

FAKE = {"generator": "fake"}
WAN_480 = {"generator": "openrouter", "model": "alibaba/wan-3.0", "resolution": "480p"}


def _request(ir, scene_key="intro", version_id="ver_1", seed=7):
    scene = ir.scene(scene_key)
    return SceneGenerationRequest(
        version_id=version_id,
        scene=scene,
        entities=tuple(ir.entity(k) for k in scene.entity_keys),
        seed=seed,
    )


def _cached(store, profile=FAKE):
    inner = FakeVideoGenerator(store)
    return CachingVideoGenerator(inner, cache=ArtifactClipCache(store, profile)), inner


# ---- 키 ------------------------------------------------------------------------------


def test_generation_key_covers_fingerprint_and_generator_profile():
    key = generation_key("fp", WAN_480)
    assert key == generation_key("fp", dict(reversed(list(WAN_480.items()))))  # 순서 무관
    assert key != generation_key("fp-other", WAN_480)
    assert key != generation_key("fp", {**WAN_480, "resolution": "720p"})
    assert key != generation_key("fp", {**WAN_480, "model": "google/veo-3.1"})
    assert key != generation_key("fp", FAKE)


def test_changing_model_or_resolution_is_a_miss(ir):
    store = InMemoryArtifactStore()
    inner = FakeVideoGenerator(store)
    by_profile = {
        name: CachingVideoGenerator(inner, cache=ArtifactClipCache(store, profile))
        for name, profile in {
            "480p": WAN_480,
            "720p": {**WAN_480, "resolution": "720p"},
            "other-model": {**WAN_480, "model": "google/veo-3.1"},
        }.items()
    }
    for generator in by_profile.values():
        generator.generate_scene(_request(ir))
    assert len(inner.requests) == 3

    by_profile["480p"].generate_scene(_request(ir, version_id="ver_2"))
    assert len(inner.requests) == 3 and by_profile["480p"].hits == 1


def test_profiles_come_from_the_wrapped_generator(ir):
    store = InMemoryArtifactStore()
    fake = FakeVideoGenerator(store)
    assert fake.profile() == {"generator": "FakeVideoGenerator"}
    assert CachingVideoGenerator(fake).profile() == fake.profile()
    assert RetryingVideoGenerator(fake, retries=2).profile() == fake.profile()


def test_openrouter_profile_names_model_output_spec_and_endpoint():
    store = InMemoryArtifactStore()
    profile = make_openrouter_generator(store, resolution="480p", fps=30).profile()
    assert profile == {
        "generator": "openrouter",
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "alibaba/wan-3.0",
        "resolution": "480p",
        "aspect_ratio": "9:16",
        "fps": 30,
    }
    # 해상도·fps·주소(가짜 서버)를 바꾸면 다른 키가 된다
    assert make_openrouter_generator(store, resolution="720p").profile() != profile
    assert make_openrouter_generator(store, resolution="480p", fps=24).profile() != profile
    local = make_openrouter_generator(
        store, resolution="480p", base_url="http://127.0.0.1:8799/api/v1"
    )
    assert local.profile() != profile


# ---- 적중 표시 ----------------------------------------------------------------------


def test_hit_marks_a_copy_and_keeps_the_original_metadata(caplog, ir):
    store = InMemoryArtifactStore()
    generator, inner = _cached(store)
    original = generator.generate_scene(_request(ir, version_id="ver_1"))

    with caplog.at_level(logging.INFO):
        hit = generator.generate_scene(_request(ir, version_id="ver_2"))

    assert len(inner.requests) == 1 and generator.hits == 1
    assert "cache_hit" not in original.metadata  # 처음 만든 클립(불변)은 그대로
    assert hit.metadata["cache_hit"] is True
    assert hit.metadata["cache_key"] == generation_key(original.fingerprint, FAKE)
    assert hit.metadata["cached_from_version_id"] == "ver_1"
    assert hit.metadata["generator"] == "fake"  # 원래 메타데이터는 남는다
    assert (hit.uri, hit.fingerprint, hit.duration_ms) == (
        original.uri,
        original.fingerprint,
        original.duration_ms,
    )
    assert any("생성 캐시 적중" in r.getMessage() for r in caplog.records)


# ---- 적중 조건: 산출물이 실제로 남아 있어야 한다 ----------------------------------------


def test_hit_requires_the_clip_artifact_to_still_exist(tmp_path, ir):
    store = LocalFileArtifactStore(tmp_path)
    generator, inner = _cached(store)
    clip = generator.generate_scene(_request(ir, version_id="ver_1"))
    store.local_path(clip.uri).unlink()  # 산출물 폴더에서 지워졌다

    again = generator.generate_scene(_request(ir, version_id="ver_2"))
    assert len(inner.requests) == 2 and generator.hits == 0
    assert store.exists(again.uri) and "cache_hit" not in again.metadata

    # 다시 만든 클립으로 캐시 항목이 바뀐다
    third = generator.generate_scene(_request(ir, version_id="ver_3"))
    assert len(inner.requests) == 2 and third.uri == again.uri


def test_unreadable_cache_entry_is_treated_as_a_miss(tmp_path, ir):
    store = LocalFileArtifactStore(tmp_path)
    generator, inner = _cached(store)
    generator.generate_scene(_request(ir))
    (entry,) = (tmp_path / "cache").rglob("*.json")
    entry.write_text("{깨진 JSON", encoding="utf-8")

    clip = generator.generate_scene(_request(ir, version_id="ver_2"))
    assert len(inner.requests) == 2 and clip.scene_key == "intro"


def test_failed_generation_is_not_cached(ir):
    store = InMemoryArtifactStore()
    inner = FakeVideoGenerator(store, fail_scene_keys={"intro"})
    generator = CachingVideoGenerator(inner, cache=ArtifactClipCache(store, FAKE))
    for _ in range(2):
        with pytest.raises(GenerationError, match="주입된 오류"):
            generator.generate_scene(_request(ir))
    assert len(inner.requests) == 2


def test_cache_write_failure_does_not_discard_the_generated_clip(caplog, ir):
    class FullCacheStore(InMemoryArtifactStore):
        def put(self, key, data, content_type=None):
            if key.startswith("cache/"):
                raise StorageError("디스크가 가득 찼습니다")
            return super().put(key, data, content_type)

    store = FullCacheStore()
    generator, inner = _cached(store)
    with caplog.at_level(logging.WARNING):
        clip = generator.generate_scene(_request(ir))
    assert store.exists(clip.uri) and len(inner.requests) == 1
    assert any("생성 캐시에 기록하지 못했습니다" in r.getMessage() for r in caplog.records)


# ---- 동시 요청 -----------------------------------------------------------------------


def test_concurrent_identical_requests_are_generated_once(ir):
    store = InMemoryArtifactStore()
    started, release = threading.Event(), threading.Event()

    class SlowFake(FakeVideoGenerator):
        def generate_scene(self, request):
            started.set()
            release.wait(5)
            return super().generate_scene(request)

    inner = SlowFake(store)
    generator = CachingVideoGenerator(inner, cache=ArtifactClipCache(store, FAKE))
    results = []

    def run(version_id):
        results.append(generator.generate_scene(_request(ir, version_id=version_id)))

    first = threading.Thread(target=run, args=("ver_1",))
    second = threading.Thread(target=run, args=("ver_2",))
    first.start()
    assert started.wait(5)
    second.start()
    time.sleep(0.05)  # 두 번째 요청이 같은 키에서 기다리게 한다
    release.set()
    first.join(5)
    second.join(5)

    assert len(inner.requests) == 1
    assert len(results) == 2 and results[0].uri == results[1].uri


# ---- 구현 선택 ------------------------------------------------------------------------


def test_cache_is_kept_in_the_artifact_store_when_it_can_address_keys(tmp_path):
    assert isinstance(build_clip_cache(LocalFileArtifactStore(tmp_path), FAKE), ArtifactClipCache)
    assert isinstance(build_clip_cache(InMemoryArtifactStore(), FAKE), ArtifactClipCache)

    class OpaqueStore(InMemoryArtifactStore):
        def uri_for(self, key):
            return None  # 키로 URI 를 계산할 수 없는 저장소 (예: 업로드 뒤 id 를 받는 원격 저장소)

    assert isinstance(build_clip_cache(OpaqueStore(), FAKE), InMemoryClipCache)


def test_in_memory_cache_is_the_default(ir):
    store = InMemoryArtifactStore()
    inner = FakeVideoGenerator(store)
    generator = CachingVideoGenerator(inner)
    generator.generate_scene(_request(ir, version_id="ver_1"))
    generator.generate_scene(_request(ir, version_id="ver_2"))
    assert len(inner.requests) == 1 and generator.hits == 1
