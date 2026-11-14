"""같은 화면의 글자 읽기는 한 번만 과금한다 (감사 BTS-12, ADR-0013 의 원칙).

화면 글자 읽기 어댑터를 감싸 결과를 산출물 저장소에 남긴다.
- 키: 클립의 sha256 + 시각 + 영역들 + 읽는 쪽의 프로필(모델). 합성한 장면 클립이 그대로면 (문구를 고치지 않은 장면)
  다시 검수해도 읽지 않는다. 문구를 고쳐 다시 합성한 장면은 클립이 달라지므로 다시 읽는다.
- 영역 가운데 하나라도 빈 답이면 남기지 않는다. 빈 답은 '글자 없음' 일 수도 '읽지 못함' 일 수도 있어,
  읽지 못한 것을 굳히지 않는다.
- 실패는 남기지 않는다. 저장된 문서가 깨졌으면 다시 읽는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence

from frame.application.errors import StorageError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.series.overlay import ScreenTextReader
from frame.domain.hashing import canonical_hash
from frame.domain.screen import Region

log = logging.getLogger(__name__)


class CachingScreenTextReader(ScreenTextReader):
    def __init__(self, inner: ScreenTextReader, store: ArtifactStore, profile: str) -> None:
        self.inner = inner
        self._store = store
        self._profile = profile

    def read(self, clip: bytes, at_ms: int, regions: Sequence[Region]) -> tuple[str, ...]:
        if not regions:
            return ()
        key = self._key(clip, at_ms, regions)
        cached = self._load(key, len(regions))
        if cached is not None:
            return cached
        texts = tuple(self.inner.read(clip, at_ms, regions))
        if all(text.strip() for text in texts):
            try:
                self._store.put(
                    key,
                    json.dumps({"texts": list(texts)}, ensure_ascii=False).encode("utf-8"),
                    "application/json",
                )
            except StorageError as exc:
                log.warning("읽은 화면 문구를 남기지 못했습니다: %s", exc)
        return texts

    def _key(self, clip: bytes, at_ms: int, regions: Sequence[Region]) -> str:
        digest = canonical_hash(
            {
                "clip": hashlib.sha256(clip).hexdigest(),
                "at_ms": int(at_ms),
                "regions": [region.to_dict() for region in regions],
                "profile": self._profile,
            }
        )
        return f"cache/screen-text/{digest}.json"

    def _load(self, key: str, count: int) -> tuple[str, ...] | None:
        uri = self._store.uri_for(key)
        if uri is None or not self._store.exists(uri):
            return None
        try:
            texts = json.loads(self._store.get(uri).decode("utf-8"))["texts"]
        except (StorageError, ValueError, KeyError, TypeError) as exc:
            log.warning("남겨 둔 화면 문구를 읽지 못해 다시 읽습니다: %s", type(exc).__name__)
            return None
        if not isinstance(texts, list) or len(texts) != count:
            return None
        return tuple(str(text) for text in texts)
