"""Decorator 패턴: 어떤 VideoGenerator 에도 재시도·결과 캐시를 덧씌운다 (리스크 1: 긴 생성 시간 대응)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, MutableMapping
from contextvars import ContextVar
from typing import Any

from frame.application.errors import GenerationError, GenerationTimeout
from frame.application.ports.generator import GeneratedClip, SceneGenerationRequest, VideoGenerator


class CachingVideoGenerator(VideoGenerator):
    """같은 생성 조건(fingerprint)의 요청은 한 번만 생성한다."""

    def __init__(
        self, inner: VideoGenerator, cache: MutableMapping[str, GeneratedClip] | None = None
    ) -> None:
        self._inner = inner
        self._cache: MutableMapping[str, GeneratedClip] = cache if cache is not None else {}
        self.hits = 0

    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip:
        key = request.fingerprint
        if key in self._cache:
            self.hits += 1
            return self._cache[key]
        clip = self._inner.generate_scene(request)
        self._cache[key] = clip
        return clip


class RetryingVideoGenerator(VideoGenerator):
    def __init__(
        self,
        inner: VideoGenerator,
        retries: int = 1,
        sleep: Callable[[float], None] | None = None,
        backoff_seconds: float = 0.0,
    ) -> None:
        self._inner = inner
        self._retries = max(0, retries)
        self._sleep = sleep or (lambda _: None)
        self._backoff = backoff_seconds
        self.attempts = 0

    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip:
        last: GenerationError | None = None
        for attempt in range(self._retries + 1):
            self.attempts += 1
            try:
                return self._inner.generate_scene(request)
            except GenerationError as exc:
                if not exc.retryable:  # 이미 과금된 외부 작업 등: 다시 제출하지 않는다
                    raise
                last = exc
                if attempt < self._retries:
                    self._sleep(self._backoff * (attempt + 1))
        assert last is not None
        raise last


# ---- 범용 시간 제한 (이슈 #1, ADR-0012) -------------------------------------------------

log = logging.getLogger(__name__)

# TimeoutVideoGenerator 가 만든 작업 스레드 안에서만 값이 있다. 마감이 지나 호출한 쪽이 먼저 돌아가면 set 된다.
_ABANDONED: ContextVar[threading.Event | None] = ContextVar(
    "frame_generation_abandoned", default=None
)


def pause_before_retry(seconds: float) -> None:
    """RetryingVideoGenerator(sleep=...) 에 꽂는 재시도 전 대기.

    시간 제한으로 버려진 작업 스레드에서는 다음 시도(외부 API 재제출 = 재과금)를 시작하지 않고
    GenerationTimeout 으로 멈춘다. 그 밖에서는 seconds 만큼 기다린다 (0 이면 바로 돌아온다).
    """
    abandoned = _ABANDONED.get()
    if abandoned is None:
        if seconds > 0:
            time.sleep(seconds)
        return
    if abandoned.is_set() or (seconds > 0 and abandoned.wait(seconds)):
        raise GenerationTimeout(
            "시간 제한으로 버려진 생성 작업이라 다시 시도하지 않습니다", retryable=False
        )


class TimeoutVideoGenerator(VideoGenerator):
    """장면 하나를 만드는 시간을 timeout_seconds 로 제한한다. 넘으면 GenerationTimeout(retryable=False).

    - 파이썬 스레드는 강제로 멈출 수 없으므로 내부 호출은 작업 스레드(daemon)에서 돌리고, 호출한 쪽만 마감에 돌아온다.
    - 버려진 작업 스레드는 내부 호출이 스스로 끝날 때까지 돈다 (OpenRouter 는 자체 제한 FRAME_VIDEO_TIMEOUT_S 까지).
      그 안의 RetryingVideoGenerator 는 pause_before_retry 를 꽂았으면 다음 시도를 하지 않는다.
    - 마감 뒤에 나온 결과는 버린다 (버전은 이미 FAILED). 과금됐을 수 있는 산출물을 회수하도록 URI 를 경고 로그로 남긴다.
    - 시간 초과는 재시도하지 않는다: 외부 작업이 접수돼 계속 돌고 있을 수 있어 다시 제출하면 또 과금된다.
    """

    def __init__(self, inner: VideoGenerator, timeout_seconds: float) -> None:
        if not timeout_seconds > 0:  # NaN 도 거른다
            raise ValueError(f"시간 제한은 0 보다 커야 합니다: {timeout_seconds}")
        self._inner = inner
        self.timeout_seconds = float(timeout_seconds)
        self.timeouts = 0
        self._lock = threading.Lock()
        self._abandoned: set[threading.Thread] = set()

    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip:
        abandoned = threading.Event()
        done = threading.Event()
        outcome: dict[str, Any] = {}

        def work() -> None:
            _ABANDONED.set(abandoned)
            try:
                outcome["clip"] = self._inner.generate_scene(request)
            except BaseException as exc:  # noqa: BLE001 - 호출한 스레드에서 그대로 다시 던진다
                outcome["error"] = exc
            finally:
                with self._lock:
                    done.set()
                    late = abandoned.is_set()
                if late:
                    try:
                        self._log_late(request, outcome)
                    finally:
                        with self._lock:
                            self._abandoned.discard(threading.current_thread())

        worker = threading.Thread(
            target=work, name=f"frame-generate-{request.scene.scene_key}", daemon=True
        )
        worker.start()
        if not done.wait(self.timeout_seconds):
            with self._lock:
                if not done.is_set():  # 마감과 완료가 겹치면 완료를 따른다
                    abandoned.set()
                    self._abandoned.add(worker)
                    self.timeouts += 1
            if abandoned.is_set():
                raise GenerationTimeout(
                    f"장면 '{request.scene.scene_key}' 생성이 {self.timeout_seconds:g}초 안에 "
                    "끝나지 않았습니다. 생성 작업은 멈추지 않고 계속될 수 있어(외부 API 는 과금) "
                    "다시 시도하지 않습니다",
                    retryable=False,
                )
        if "error" in outcome:
            raise outcome["error"]
        return outcome["clip"]

    def wait_abandoned(self, timeout: float | None = None) -> bool:
        """버려진 작업 스레드가 모두 끝날 때까지 기다린다 (테스트·종료 정리용). 모두 끝났으면 True."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._lock:
            workers = list(self._abandoned)
        for worker in workers:
            worker.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
        with self._lock:
            return not self._abandoned

    def _log_late(self, request: SceneGenerationRequest, outcome: dict[str, Any]) -> None:
        scene, version = request.scene.scene_key, request.version_id
        if "error" in outcome:
            log.warning(
                "시간 제한(%gs) 뒤 장면 %s(버전 %s) 생성이 실패로 끝났습니다: %s",
                self.timeout_seconds,
                scene,
                version,
                outcome["error"],
            )
            return
        log.warning(
            "시간 제한(%gs) 뒤 장면 %s(버전 %s) 생성이 끝났지만 결과를 버립니다. "
            "과금됐을 수 있으니 필요하면 %s 를 직접 회수하세요",
            self.timeout_seconds,
            scene,
            version,
            outcome["clip"].uri,
        )
