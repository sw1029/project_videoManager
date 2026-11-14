"""생성 결과 캐시 (#7, ADR-0013): (fingerprint, 생성기 프로필) -> 장면 클립.

- 키는 generation_key(fingerprint, profile) 이다. fingerprint 는 IR 조건(장면·객체·참조 이미지·seed)만 담으므로
  생성기 설정(종류·모델·해상도·화면비·fps·주소)을 프로필로 함께 넣어, 설정을 바꾸면 이전 결과가 적중하지 않게 한다.
- ArtifactClipCache 는 항목을 ArtifactStore 의 cache/clips/<키>.json 에 둔다. 클립과 같은 저장소에 있으므로
  산출물 폴더를 지우면 캐시도 함께 사라지고, local_fs 면 프로세스를 다시 시작해도 남는다.
- 적중은 클립 산출물이 저장소에 실제로 남아 있을 때만이다. 읽을 수 없는 항목은 없는 것으로 본다 (다시 생성).
- KeyedLocks 는 같은 키의 동시 요청을 하나로 줄인다 (같은 장면을 두 번 제출해 두 번 과금되는 일을 막는다).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from frame.application.errors import StorageError
from frame.application.ports.artifact_store import ArtifactStore
from frame.domain.artifacts import GeneratedClip
from frame.domain.hashing import canonical_hash

log = logging.getLogger(__name__)

CACHE_PREFIX = "cache/clips"
_SCHEMA = 1


def generation_key(fingerprint: str, profile: Mapping[str, object]) -> str:
    """요청 쪽 키와 생성기 프로필을 함께 해시한 키. 생성 캐시와 작업 원장이 같이 쓴다.

    fingerprint 자리에는 VideoGenerator.result_key(request) 를 넣는다. 정체성 기준 이미지도 부모 클립 편집도
    없는 요청에서는 조건 fingerprint 와 같아, 예전 키와 호환된다 (ADR-0015, 0016).
    """
    return canonical_hash({"fingerprint": fingerprint, "profile": dict(profile)})


@dataclass(frozen=True)
class CachedClip:
    clip: GeneratedClip
    version_id: str | None = None  # 처음 만든 버전 (적중 메타데이터에 남긴다)


class ClipCache(ABC):
    """한 생성기(프로필)의 생성 결과 캐시. 키 계산에 쓸 프로필을 만들 때 고정한다."""

    def __init__(self, profile: Mapping[str, object]) -> None:
        self.profile: dict[str, object] = dict(profile)

    def key_for(self, fingerprint: str) -> str:
        return generation_key(fingerprint, self.profile)

    @abstractmethod
    def load(self, key: str) -> CachedClip | None:
        """쓸 수 있는 항목만 돌려준다 (클립 산출물이 사라졌거나 항목이 깨졌으면 None)."""

    @abstractmethod
    def save(self, key: str, clip: GeneratedClip, *, version_id: str) -> None: ...


class InMemoryClipCache(ClipCache):
    """프로세스 메모리 캐시. 키로 URI 를 계산할 수 없는 저장소에서 쓰는 대안 (재시작하면 사라진다)."""

    def __init__(
        self, profile: Mapping[str, object] | None = None, store: ArtifactStore | None = None
    ) -> None:
        super().__init__(profile or {})
        self._store = store
        self._entries: dict[str, CachedClip] = {}

    def load(self, key: str) -> CachedClip | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._store is not None and not self._store.exists(entry.clip.uri):
            return None
        return entry

    def save(self, key: str, clip: GeneratedClip, *, version_id: str) -> None:
        self._entries[key] = CachedClip(clip, version_id)


class ArtifactClipCache(ClipCache):
    """ArtifactStore 에 JSON 으로 영속하는 캐시. 저장소는 uri_for(키) 를 지원해야 한다."""

    def __init__(
        self,
        store: ArtifactStore,
        profile: Mapping[str, object],
        *,
        prefix: str = CACHE_PREFIX,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(profile)
        if store.uri_for(f"{prefix}/probe.json") is None:
            raise ValueError(
                "ArtifactClipCache 는 키로 URI 를 계산하는 저장소(uri_for)가 필요합니다"
            )
        self._store = store
        self._prefix = prefix.strip("/")
        self._wall_clock = wall_clock

    def load(self, key: str) -> CachedClip | None:
        uri = self._uri(key)
        try:
            if not self._store.exists(uri):
                return None
            data = json.loads(self._store.get(uri).decode("utf-8"))
            entry = _decode(data, key)
        except (StorageError, OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("생성 캐시 항목을 읽지 못해 없는 것으로 봅니다: %s (%s)", uri, exc)
            return None
        if not self._store.exists(entry.clip.uri):
            log.info("생성 캐시 항목의 클립이 저장소에 없어 다시 만듭니다: %s", entry.clip.uri)
            return None
        return entry

    def save(self, key: str, clip: GeneratedClip, *, version_id: str) -> None:
        payload = {
            "schema": _SCHEMA,
            "key": key,
            "profile": self.profile,
            "version_id": version_id,
            "saved_at": self._wall_clock(),
            "clip": {
                "scene_key": clip.scene_key,
                "uri": clip.uri,
                "duration_ms": clip.duration_ms,
                "fingerprint": clip.fingerprint,
                "metadata": dict(clip.metadata),
            },
        }
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        self._store.put(self._path(key), data.encode("utf-8"), "application/json")

    def _path(self, key: str) -> str:
        return f"{self._prefix}/{key}.json"

    def _uri(self, key: str) -> str:
        uri = self._store.uri_for(self._path(key))
        assert uri is not None  # 생성자에서 확인했다
        return uri


def _decode(data: Any, key: str) -> CachedClip:
    if not isinstance(data, dict) or data.get("schema") != _SCHEMA or data.get("key") != key:
        raise ValueError("알 수 없는 형식")
    raw = data["clip"]
    clip = GeneratedClip(
        scene_key=str(raw["scene_key"]),
        uri=str(raw["uri"]),
        duration_ms=int(raw["duration_ms"]),
        fingerprint=str(raw["fingerprint"]),
        metadata=dict(raw.get("metadata") or {}),
    )
    version_id = data.get("version_id")
    return CachedClip(clip, str(version_id) if version_id is not None else None)


def build_clip_cache(store: ArtifactStore | None, profile: Mapping[str, object]) -> ClipCache:
    """저장소가 키로 URI 를 계산할 수 있으면 영속 캐시, 아니면 프로세스 메모리 캐시."""
    if store is not None and store.uri_for(f"{CACHE_PREFIX}/probe.json") is not None:
        return ArtifactClipCache(store, profile)
    log.warning("산출물 저장소가 uri_for 를 지원하지 않아 생성 캐시를 프로세스 메모리에만 둡니다")
    return InMemoryClipCache(profile, store=store)


class KeyedLocks:
    """키별 잠금 (같은 키의 작업을 한 번에 하나만). 쓰는 쪽이 없어지면 잠금도 지운다."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, tuple[threading.Lock, list[int]]] = {}

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        """그 키의 잠금을 얻을 때까지 기다렸다가, 블록이 끝나면 푼다."""
        lock = self._enter(key)
        try:
            with lock:
                yield
        finally:
            self._leave(key)

    @contextmanager
    def try_hold(self, key: str) -> Iterator[bool]:
        """기다리지 않고 얻어 본다. 얻었으면 True 를 내주고 블록이 끝나면 푼다. 다른 쪽이 쥐고 있으면 False 를 내준다.

        남의 키에 잠깐 쓰려는 쪽이 쓴다: 그 키로 누가 일하는 중이면 끼어들지 않고 물러난다.
        """
        lock = self._enter(key)
        acquired = lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()
            self._leave(key)

    def _enter(self, key: str) -> threading.Lock:
        with self._guard:
            lock, users = self._locks.setdefault(key, (threading.Lock(), [0]))
            users[0] += 1
        return lock

    def _leave(self, key: str) -> None:
        with self._guard:
            users = self._locks[key][1]
            users[0] -= 1
            if users[0] == 0:
                del self._locks[key]
