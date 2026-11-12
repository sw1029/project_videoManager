"""Decorator 패턴: 어떤 VideoGenerator 에도 재시도·결과 캐시를 덧씌운다 (리스크 1: 긴 생성 시간 대응)."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping

from frame.application.errors import GenerationError
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
