"""생성기 Decorator 체인과 범용 시간 제한 설정 (이슈 #1, ADR-0012).

체인(안쪽 -> 바깥): 실제 생성기 -> Retry -> Timeout -> Cache
- 캐시 적중은 시간 제한(작업 스레드)을 거치지 않는다.
- 시간 초과는 Retry 바깥에서 나므로 재시도되지 않는다 (GenerationTimeout 은 retryable=False 이기도 하다).
"""

import logging
import threading

import pytest

from frame.adapters.generator import (
    CachingVideoGenerator,
    FakeVideoGenerator,
    RetryingVideoGenerator,
    TimeoutVideoGenerator,
)
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import GenerationError, GenerationTimeout
from frame.application.ports.generator import SceneGenerationRequest, VideoGenerator
from frame.config import Settings
from tests.support.factories import cafe_ir, make_container


def _chain(generator):
    kinds = []
    while generator is not None:
        kinds.append(type(generator))
        generator = getattr(generator, "_inner", None)
    return kinds


def _request(scene_key="intro", version_id="ver_1"):
    ir = cafe_ir()
    scene = ir.scene(scene_key)
    return SceneGenerationRequest(
        version_id=version_id,
        scene=scene,
        entities=tuple(ir.entity(k) for k in scene.entity_keys),
        seed=7,
    )


def test_generator_timeout_is_off_by_default():
    assert Settings().generator_timeout_s == 0
    assert Settings.from_env({}).generator_timeout_s == 0


@pytest.mark.parametrize(
    "raw, expected",
    [("900", 900.0), ("12.5", 12.5), ("0", 0.0), ("", 0.0), ("off", 0.0), ("none", 0.0)],
)
def test_generator_timeout_is_parsed_from_env(raw, expected):
    assert Settings.from_env({"FRAME_GENERATOR_TIMEOUT_S": raw}).generator_timeout_s == expected


def test_container_chains_retry_then_timeout_then_cache():
    settings = Settings(
        jobs="recording", generator_retries=1, generator_timeout_s=30, generator_cache=True
    )
    generator = make_container(settings).generator
    assert _chain(generator) == [
        CachingVideoGenerator,
        TimeoutVideoGenerator,
        RetryingVideoGenerator,
        FakeVideoGenerator,
    ]
    assert generator._inner.timeout_seconds == 30


@pytest.mark.parametrize("seconds", [0, -5])
def test_timeout_decorator_is_left_out_when_off(seconds):
    settings = Settings(
        jobs="recording", generator_retries=1, generator_timeout_s=seconds, generator_cache=True
    )
    assert TimeoutVideoGenerator not in _chain(make_container(settings).generator)


def test_container_wiring_never_resubmits_after_a_timeout():
    """시간 초과로 버려진 작업 스레드 안에서 Retry 가 '일시 오류' 를 받아도 다시 제출하지 않는다 (재과금 방지)."""
    release = threading.Event()

    class SlowThenTransientError(VideoGenerator):
        calls = 0

        def generate_scene(self, request):
            self.calls += 1
            release.wait(5)
            raise GenerationError("일시 오류")  # retryable=True

    base = SlowThenTransientError()
    settings = Settings(jobs="recording", generator_retries=3, generator_timeout_s=0.05)
    generator = make_container(settings, generator=base).generator
    try:
        with pytest.raises(GenerationTimeout):
            generator.generate_scene(_request())
    finally:
        release.set()
    assert generator.wait_abandoned(5)
    assert base.calls == 1


def test_cache_hit_skips_the_timeout_and_the_generator():
    store = InMemoryArtifactStore()
    base = FakeVideoGenerator(store)
    settings = Settings(jobs="recording", generator_timeout_s=30, generator_cache=True)
    generator = make_container(settings, generator=base).generator

    first = generator.generate_scene(_request(version_id="ver_1"))
    second = generator.generate_scene(_request(version_id="ver_2"))  # 같은 생성 조건

    assert second == first and len(base.requests) == 1 and generator.hits == 1


def test_warns_when_generic_timeout_would_cut_before_the_openrouter_timeout(caplog):
    """범용 시간 제한은 어댑터 자체 제한(FRAME_VIDEO_TIMEOUT_S)보다 길어야 job id 가 담긴 오류가 먼저 난다."""
    settings = Settings(
        jobs="recording",
        generator="external_api",
        generator_timeout_s=600,
        video_timeout_s=600,
    )
    with caplog.at_level(logging.WARNING, logger="frame.config.container"):
        make_container(settings, generator=FakeVideoGenerator(InMemoryArtifactStore()))
    assert any("FRAME_GENERATOR_TIMEOUT_S" in r.getMessage() for r in caplog.records)


def test_no_warning_when_generic_timeout_is_longer(caplog):
    settings = Settings(
        jobs="recording",
        generator="external_api",
        generator_timeout_s=900,
        video_timeout_s=600,
    )
    with caplog.at_level(logging.WARNING, logger="frame.config.container"):
        make_container(settings, generator=FakeVideoGenerator(InMemoryArtifactStore()))
    assert not any("FRAME_GENERATOR_TIMEOUT_S" in r.getMessage() for r in caplog.records)
