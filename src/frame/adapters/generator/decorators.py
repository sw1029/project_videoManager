"""Decorator 패턴: 어떤 VideoGenerator 에도 재시도·결과 캐시를 덧씌운다 (리스크 1: 긴 생성 시간 대응)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import replace

from frame.application.errors import GenerationError
from frame.application.ports.generator import GeneratedClip, SceneGenerationRequest, VideoGenerator

from .cache import ClipCache, InMemoryClipCache, KeyedLocks

log = logging.getLogger(__name__)


class CachingVideoGenerator(VideoGenerator):
    """같은 생성 조건(fingerprint)·같은 생성기 프로필의 요청은 한 번만 생성한다 (#7, ADR-0013).

    - cache 를 주지 않으면 프로세스 메모리 캐시다. 컨테이너는 ArtifactStore 에 영속하는 캐시를 넘긴다.
    - 같은 키의 동시 요청은 하나만 생성하고 나머지는 그 결과를 쓴다.
    - 적중한 클립은 복사본의 메타데이터에 cache_hit·cache_key·cached_from_version_id 를 남긴다
      (GeneratedClip 은 불변). 원래 메타데이터(비용 등)는 처음 생성할 때의 값이므로 합산하지 않는다.
    - 생성 실패는 캐시하지 않는다. 캐시 기록이 실패해도 생성한 클립은 그대로 돌려준다.
    """

    def __init__(self, inner: VideoGenerator, cache: ClipCache | None = None) -> None:
        self._inner = inner
        self._cache = cache if cache is not None else InMemoryClipCache(inner.profile())
        self._locks = KeyedLocks()
        self.hits = 0

    def profile(self) -> Mapping[str, object]:
        return self._inner.profile()

    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip:
        key = self._cache.key_for(request.fingerprint)
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
            try:
                self._cache.save(key, clip, version_id=request.version_id)
            except Exception as exc:  # noqa: BLE001 - 캐시 기록 실패가 생성(과금)한 결과를 버리게 하지 않는다
                log.warning("생성 캐시에 기록하지 못했습니다: key=%s (%s)", key[:12], exc)
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

    def profile(self) -> Mapping[str, object]:
        return self._inner.profile()

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
