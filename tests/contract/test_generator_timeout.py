"""TimeoutVideoGenerator 계약 (이슈 #1): 느린 생성기를 마감에 끊고, 끊은 작업은 다시 시도하지 않는다.

파이썬 스레드는 강제로 멈출 수 없으므로 내부 호출은 작업 스레드에서 돌고, 마감이 지나면 호출한 쪽만 먼저 돌아온다.
느린 생성기는 실제 sleep 대신 Event 로 막아 결정적으로 시험한다 (테스트 끝에서 풀어 준다).
"""

import logging
import threading
import time

import pytest

from frame.adapters.generator import (
    FakeVideoGenerator,
    RetryingVideoGenerator,
    TimeoutVideoGenerator,
)
from frame.adapters.generator.decorators import pause_before_retry
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import GenerationError, GenerationTimeout
from frame.application.ports.generator import SceneGenerationRequest, VideoGenerator

WAIT = 5.0  # 테스트가 멈춰 버리지 않도록 하는 안전 상한 (정상이면 바로 끝난다)


class SlowGenerator(VideoGenerator):
    """release 될 때까지 끝나지 않는 느린 생성기 (실제 모델의 긴 생성 시간을 흉내)."""

    def __init__(self, inner: VideoGenerator, release: threading.Event, error=None) -> None:
        self.inner, self.release, self.error = inner, release, error
        self.calls = 0

    def generate_scene(self, request):
        self.calls += 1
        self.release.wait(WAIT)
        if self.error is not None:
            raise self.error
        return self.inner.generate_scene(request)


@pytest.fixture
def store():
    return InMemoryArtifactStore()


@pytest.fixture
def release():
    event = threading.Event()
    yield event
    event.set()  # 실패한 테스트도 작업 스레드를 남기지 않게


def _request(ir, scene_key="intro"):
    scene = ir.scene(scene_key)
    return SceneGenerationRequest(
        version_id="ver_1",
        scene=scene,
        entities=tuple(ir.entity(k) for k in scene.entity_keys),
        seed=7,
    )


def test_slow_generation_times_out_without_waiting_for_the_inner_call(store, release, ir):
    slow = SlowGenerator(FakeVideoGenerator(store), release)
    generator = TimeoutVideoGenerator(slow, timeout_seconds=0.05)

    started = time.monotonic()
    with pytest.raises(GenerationTimeout) as excinfo:
        generator.generate_scene(_request(ir))

    assert time.monotonic() - started < WAIT / 2  # 내부 호출이 끝나기를 기다리지 않았다
    assert excinfo.value.code == "GENERATION_TIMEOUT"
    assert excinfo.value.retryable is False  # 외부 작업이 이미 접수됐을 수 있어 재제출(재과금) 금지
    assert "intro" in str(excinfo.value) and "0.05" in str(excinfo.value)
    assert generator.timeouts == 1


def test_fast_generation_returns_the_inner_result(store, ir):
    inner = FakeVideoGenerator(store)
    generator = TimeoutVideoGenerator(inner, timeout_seconds=WAIT)
    clip = generator.generate_scene(_request(ir))
    assert clip.scene_key == "intro" and store.exists(clip.uri)
    assert len(inner.requests) == 1 and generator.timeouts == 0


def test_inner_errors_propagate_unchanged(store, ir):
    error = GenerationError("작업 실패", retryable=False)
    generator = TimeoutVideoGenerator(FakeVideoGenerator(store, error=error), timeout_seconds=WAIT)
    with pytest.raises(GenerationError) as excinfo:
        generator.generate_scene(_request(ir))
    assert excinfo.value is error  # 같은 예외 객체 (code·retryable 그대로)


def test_non_generation_errors_also_propagate(store, ir):
    generator = TimeoutVideoGenerator(
        FakeVideoGenerator(store, error=RuntimeError("boom")), timeout_seconds=WAIT
    )
    with pytest.raises(RuntimeError, match="boom"):
        generator.generate_scene(_request(ir))


@pytest.mark.parametrize("seconds", [0, -1, float("nan")])
def test_non_positive_timeout_is_rejected(store, seconds):
    with pytest.raises(ValueError):
        TimeoutVideoGenerator(FakeVideoGenerator(store), timeout_seconds=seconds)


def test_abandoned_worker_does_not_start_another_attempt(store, release, ir):
    """마감 뒤 내부 Retry 가 다음 시도(재제출)를 시작하지 않는다: 버려진 스레드가 재과금하지 않게."""
    flaky = SlowGenerator(FakeVideoGenerator(store), release, error=GenerationError("일시 오류"))
    generator = TimeoutVideoGenerator(
        RetryingVideoGenerator(flaky, retries=3, sleep=pause_before_retry), timeout_seconds=0.05
    )
    with pytest.raises(GenerationTimeout):
        generator.generate_scene(_request(ir))

    release.set()  # 첫 시도가 '일시 오류(retryable)' 로 끝난다 -> Retry 가 다음 시도를 하려 한다
    assert generator.wait_abandoned(WAIT)
    assert flaky.calls == 1


def test_retry_still_retries_outside_an_abandoned_worker(store, ir):
    """같은 대기 함수를 꽂아도, 시간 제한이 없거나 마감 전이면 평소처럼 재시도한다."""

    class FailsOnce(VideoGenerator):
        def __init__(self):
            self.calls, self.inner = 0, FakeVideoGenerator(store)

        def generate_scene(self, request):
            self.calls += 1
            if self.calls == 1:
                raise GenerationError("일시 오류")
            return self.inner.generate_scene(request)

    plain = FailsOnce()
    RetryingVideoGenerator(plain, retries=1, sleep=pause_before_retry).generate_scene(_request(ir))
    assert plain.calls == 2

    guarded = FailsOnce()
    TimeoutVideoGenerator(
        RetryingVideoGenerator(guarded, retries=1, sleep=pause_before_retry),
        timeout_seconds=WAIT,
    ).generate_scene(_request(ir))
    assert guarded.calls == 2


def test_late_result_is_discarded_and_logged(store, release, ir, caplog):
    """마감 뒤 끝난 결과는 버리되(버전은 이미 FAILED), 과금된 산출물을 손으로 회수할 수 있게 URI 를 남긴다."""
    slow = SlowGenerator(FakeVideoGenerator(store), release)
    generator = TimeoutVideoGenerator(slow, timeout_seconds=0.05)
    with pytest.raises(GenerationTimeout):
        generator.generate_scene(_request(ir))

    with caplog.at_level(logging.WARNING, logger="frame.adapters.generator.decorators"):
        release.set()
        assert generator.wait_abandoned(WAIT)

    late = [r.getMessage() for r in caplog.records if "intro" in r.getMessage()]
    assert late and "clips/ver_1/intro.json" in late[0]
    assert store.exists("mem://clips/ver_1/intro.json")  # 결과 파일은 저장소에 남아 있다


def test_wait_abandoned_reports_workers_still_running(store, release, ir):
    generator = TimeoutVideoGenerator(
        SlowGenerator(FakeVideoGenerator(store), release), timeout_seconds=0.05
    )
    with pytest.raises(GenerationTimeout):
        generator.generate_scene(_request(ir))
    assert generator.wait_abandoned(0.01) is False  # 아직 내부 호출이 끝나지 않았다
    release.set()
    assert generator.wait_abandoned(WAIT) is True


def test_profile_is_the_wrapped_generators():
    """다른 데코레이터처럼 감싼 생성기의 생성 프로필(생성 캐시 키에 쓰임)을 그대로 돌려준다."""

    class Profiled(VideoGenerator):
        def generate_scene(self, request):
            raise NotImplementedError

        def profile(self):
            return {"generator": "openrouter", "resolution": "480p"}

    generator = TimeoutVideoGenerator(Profiled(), timeout_seconds=WAIT)
    assert generator.profile() == {"generator": "openrouter", "resolution": "480p"}
