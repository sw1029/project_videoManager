"""같은 소리의 음성 인식은 한 번만 과금한다 (감사 BTS-12, ADR-0013 의 원칙).

인식기를 감싸 결과(받아쓰기)를 산출물 저장소에 남긴다. 생성기의 CachingVideoGenerator 와 같은 모양이다.
- 키: 소리의 sha256 + 인식기의 프로필(모델·주소) + 언어 + 힌트. 답을 바꿀 수 있는 것은 모두 키에 든다.
- 실패는 남기지 않는다 (다음에 다시 인식한다). 저장된 문서가 깨졌으면 없는 것으로 보고 다시 인식해 덮어쓴다.
- 저장소가 키로 주소를 지을 수 없으면(uri_for 가 None) 그대로 인식기를 부른다.
테마 추출이 뒤에서 실패해 다시 부르거나, 같은 녹음을 다시 붙여도 인식은 다시 과금되지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging

from frame.application.errors import StorageError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.speech import SpeechAudio, SpeechRecognizer
from frame.domain.errors import DomainError
from frame.domain.hashing import canonical_hash
from frame.domain.series.source_analysis import Transcript

log = logging.getLogger(__name__)


class CachingSpeechRecognizer(SpeechRecognizer):
    def __init__(self, inner: SpeechRecognizer, store: ArtifactStore, profile: str) -> None:
        self.inner = inner
        self._store = store
        self._profile = profile  # 인식기의 모델·주소. 바뀌면 예전 결과를 쓰지 않는다

    def transcribe(
        self, audio: SpeechAudio, *, language: str | None = None, hint: str = ""
    ) -> Transcript:
        key = self._key(audio, language, hint)
        cached = self._load(key)
        if cached is not None:
            return cached
        transcript = self.inner.transcribe(audio, language=language, hint=hint)
        document = {"profile": self._profile, "transcript": transcript.to_dict()}
        try:
            self._store.put(
                key, json.dumps(document, ensure_ascii=False).encode("utf-8"), "application/json"
            )
        except StorageError as exc:  # 남기지 못해도 인식 결과는 돌려준다
            log.warning("인식 결과를 남기지 못했습니다: %s", exc)
        return transcript

    def _key(self, audio: SpeechAudio, language: str | None, hint: str) -> str:
        digest = canonical_hash(
            {
                "audio": hashlib.sha256(audio.data).hexdigest(),
                "profile": self._profile,
                "language": (language or "").strip().lower(),
                "hint": " ".join(hint.split()),
            }
        )
        return f"cache/speech/{digest}.json"

    def _load(self, key: str) -> Transcript | None:
        uri = self._store.uri_for(key)
        if uri is None or not self._store.exists(uri):
            return None
        try:
            document = json.loads(self._store.get(uri).decode("utf-8"))
            return Transcript.from_dict(document["transcript"])
        except (StorageError, ValueError, KeyError, TypeError, AttributeError, DomainError) as exc:
            log.warning("남겨 둔 인식 결과를 읽지 못해 다시 인식합니다: %s", type(exc).__name__)
            return None
