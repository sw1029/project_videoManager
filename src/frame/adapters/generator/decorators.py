"""Decorator 패턴: 어떤 VideoGenerator 에도 결과 캐시·재시도·시간 제한을 덧씌운다 (리스크 1: 긴 생성 시간 대응).

DelegatingVideoGenerator 가 바탕이다. 새 데코레이터는 이것을 상속하고 바꾸려는 동작만 재정의한다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from frame.adapters.shared.locking import KeyedLocks
from frame.application.errors import GenerationError, GenerationTimeout
from frame.application.ports.generator import (
    GeneratedClip,
    ProductionPreview,
    SceneGenerationRequest,
    VideoGenerator,
)

from .abandonment import enter_worker, pause_unless_abandoned
from .cache import ClipCache, InMemoryClipCache

log = logging.getLogger(__name__)


class DelegatingVideoGenerator(VideoGenerator):
    """감싼 생성기에 그대로 넘기는 바탕 (Decorator). 하위 클래스는 바꾸려는 동작만 재정의한다.

    포트의 선택 메서드(profile · result_key · preview)는 감싼 생성기의 값을 그대로 돌려준다. 데코레이터마다 따로
    적으면 포트에 메서드가 더해질 때 하나라도 빠뜨리기 쉽고, 빠뜨리면 포트의 기본 구현이 조용히 답한다
    (예: 부모 클립이 다른 편집 결과가 같은 키로 캐시된다). 위임은 여기 한 곳에만 둔다.
    """

    def __init__(self, inner: VideoGenerator) -> None:
        self._inner = inner

    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip:
        return self._inner.generate_scene(request)

    def profile(self) -> Mapping[str, object]:
        return self._inner.profile()

    def result_key(self, request: SceneGenerationRequest) -> str:
        return self._inner.result_key(request)

    def preview(self, request: SceneGenerationRequest) -> ProductionPreview | None:
        return self._inner.preview(request)


class CachingVideoGenerator(DelegatingVideoGenerator):
    """같은 생성 조건(fingerprint)·같은 생성기 프로필의 요청은 한 번만 생성한다 (#7, ADR-0013).

    - cache 를 주지 않으면 프로세스 메모리 캐시다. 컨테이너는 ArtifactStore 에 영속하는 캐시를 넘긴다.
    - 같은 키의 동시 요청은 하나만 생성하고 나머지는 그 결과를 쓴다.
    - 적중한 클립은 복사본의 메타데이터에 cache_hit·cache_key·cached_from_version_id 를 남긴다
      (GeneratedClip 은 불변). 원래 메타데이터(비용 등)는 처음 생성할 때의 값이므로 합산하지 않는다.
    - 생성 실패는 캐시하지 않는다. 캐시 기록이 실패해도 생성한 클립은 그대로 돌려준다.
    - 생성기가 임시 결과(metadata.provisional)로 표시한 클립은 캐시하지 않는다 (ADR-0021). 요청한 방식으로 만들지 못한
      결과라서, 다음에 같은 요청이 오면 생성기까지 가서 다시 시도해야 한다.
    - 미리보기는 감싼 생성기의 값 그대로다. 캐시에 있는 결과면 실제로는 더 빨리·과금 없이 끝난다.
    """

    def __init__(self, inner: VideoGenerator, cache: ClipCache | None = None) -> None:
        super().__init__(inner)
        self._cache = cache if cache is not None else InMemoryClipCache(inner.profile())
        self._locks = KeyedLocks()
        self.hits = 0

    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip:
        # 키의 요청 쪽 값은 생성기가 정한다: 조건 + 정체성 기준 이미지 (+ 편집하는 생성기는 부모 클립)
        key = self._cache.key_for(self._inner.result_key(request))
        with self._locks.hold(key):
            cached = self._cache.load(key)
            if cached is not None:
                self.hits += 1
                log.info(
                    "생성 캐시 적중: version=%s scene=%s key=%s (처음 만든 버전 %s, 클립 %s)",
                    request.version_id,
                    request.scene.scene_key,
                    key[:12],
                    cached.version_id,
                    cached.clip.uri,
                )
                metadata = {
                    **cached.clip.metadata,
                    "cache_hit": True,
                    "cache_key": key,
                    "cached_from_version_id": cached.version_id,
                }
                return replace(cached.clip, metadata=metadata)
            clip = self._inner.generate_scene(request)
            if clip.metadata.get("provisional") is True:
                log.info(
                    "임시 결과라 생성 캐시에 남기지 않습니다: version=%s scene=%s key=%s",
                    request.version_id,
                    request.scene.scene_key,
                    key[:12],
                )
                return clip
            try:
                self._cache.save(key, clip, version_id=request.version_id)
            except Exception as exc:  # noqa: BLE001 - 캐시 기록 실패가 생성(과금)한 결과를 버리게 하지 않는다
                log.warning("생성 캐시에 기록하지 못했습니다: key=%s (%s)", key[:12], exc)
            return clip


class RetryingVideoGenerator(DelegatingVideoGenerator):
    """다시 해 볼 만한 실패(GenerationError.retryable)를 retries 번까지 다시 시도한다 (ADR-0011).

    retryable=False 인 실패는 다시 하지 않는다: 외부 API 가 이미 작업을 받아 다시 제출하면 또 과금되는 실패다.
    """

    def __init__(
        self,
        inner: VideoGenerator,
        retries: int,  # 설정(FRAME_GENERATOR_RETRIES)에서 온다
        sleep: Callable[[float], None] | None = None,
        backoff_seconds: float = 0.0,
    ) -> None:
        super().__init__(inner)
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


def pause_before_retry(seconds: float) -> None:
    """RetryingVideoGenerator(sleep=...) 에 꽂는 재시도 전 대기.

    시간 제한으로 버려진 작업 스레드에서는 다음 시도(외부 API 재제출 = 재과금)를 시작하지 않고
    GenerationTimeout 으로 멈춘다. 그 밖에서는 seconds 만큼 기다린다 (0 이면 바로 돌아온다).
    버려졌다는 신호는 abandonment 모듈에 있고, 외부 API 어댑터도 제출 직전에 같은 신호를 본다.
    """
    pause_unless_abandoned(seconds, "다시 시도하지 않습니다")


class TimeoutVideoGenerator(DelegatingVideoGenerator):
    """장면 하나를 만드는 시간을 timeout_seconds 로 제한한다. 넘으면 GenerationTimeout(retryable=False).

    - 파이썬 스레드는 강제로 멈출 수 없으므로 내부 호출은 작업 스레드(daemon)에서 돌리고, 호출한 쪽만 마감에 돌아온다.
    - 버려진 작업 스레드는 내부 호출이 스스로 끝날 때까지 돈다 (OpenRouter 는 자체 제한 FRAME_VIDEO_TIMEOUT_S 까지).
      그 안의 RetryingVideoGenerator 는 pause_before_retry 를 꽂았으면 다음 시도를 하지 않고,
      OpenRouter 어댑터는 버려진 뒤에는 제출(POST)하지 않는다 (abandonment 모듈의 신호).
    - 마감 뒤에 나온 결과는 버린다 (버전은 이미 FAILED). 과금됐을 수 있는 산출물을 회수하도록 URI 를 경고 로그로 남긴다.
    - 시간 초과는 재시도하지 않는다: 외부 작업이 접수돼 계속 돌고 있을 수 있어 다시 제출하면 또 과금된다.
    - 미리보기는 생성이 아니므로 시간 제한을 걸지 않는다 (감싼 생성기가 스스로 짧게 끝낸다).
    """

    def __init__(self, inner: VideoGenerator, timeout_seconds: float) -> None:
        if not timeout_seconds > 0:  # NaN 도 거른다
            raise ValueError(f"시간 제한은 0 보다 커야 합니다: {timeout_seconds}")
        super().__init__(inner)
        self.timeout_seconds = float(timeout_seconds)
        self.timeouts = 0
        self._lock = threading.Lock()
        self._abandoned: set[threading.Thread] = set()

    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip:
        abandoned = threading.Event()
        done = threading.Event()
        outcome: dict[str, Any] = {}

        def work() -> None:
            enter_worker(abandoned)
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
