"""같은 소리의 음성 인식은 한 번만 과금한다 (감사 BTS-12, ADR-0013 의 원칙).

인식기를 감싸 결과(받아쓰기)를 산출물 저장소에 남긴다. 생성기의 CachingVideoGenerator 와 같은 모양이다.
- 키: 소리의 sha256 + 인식기의 정체(signature: 모델·주소, 로컬은 모델의 지문·연산 방식) + 언어 + 힌트.
  답을 바꿀 수 있는 것은 모두 키에 든다. 이 컴퓨터의 경로는 들지 않는다 (감사 BTS-D66).
  남기는 받아쓰기의 형식 판(TRANSCRIPT_FORMAT)도 든다: 형식이 바뀌면 옛 결과를 읽지 않는다 (감사 BTS-D10).
- 실패는 남기지 않는다 (다음에 다시 인식한다). 저장된 문서가 깨졌으면 없는 것으로 보고 다시 인식해 덮어쓴다.
- 남기지 못해도 결과는 돌려준다. 저장소의 오류(StorageError)와 파일 쓰기 오류(OSError) 둘 다다: 방금 과금한 결과를
  저장 실패 때문에 버리지 않는다 (감사 BTS-D04·D35).
- 포트의 계약(시간 순서·겹치지 않음·소리 길이 안)을 어긴 결과도 남기지 않는다. 남기면 다시 불러도 같은 받아쓰기가
  나와 그 소리는 계속 실패한다 (감사 BTS-D61). 결과는 그대로 돌려준다 (부른 쪽이 검사한다).
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
from frame.domain.errors import DomainError, ValidationError
from frame.domain.hashing import canonical_hash
from frame.domain.speech import Transcript

log = logging.getLogger(__name__)
# 남기는 받아쓰기의 형식 판. 형식을 바꾸면 올린다 (옛 결과를 쓰지 않는다).
# 2: 구간에 신뢰도·말이 아닐 가능성이 들고, 계약에 맞춘(시간 순서·겹치지 않음·길이 안) 받아쓰기만 남긴다
TRANSCRIPT_FORMAT = 2


class CachingSpeechRecognizer(SpeechRecognizer):
    def __init__(
        self, inner: SpeechRecognizer, store: ArtifactStore, profile: str | None = None
    ) -> None:
        self.inner = inner
        self._store = store
        # 인식기의 정체. 바뀌면 예전 결과를 쓰지 않는다. 따로 주지 않으면 인식기가 말하는 것을 쓴다
        self._profile = profile or inner.signature

    @property
    def signature(self) -> str:
        return self._profile

    def transcribe(
        self, audio: SpeechAudio, *, language: str | None = None, hint: str = ""
    ) -> Transcript:
        key = self._key(audio, language, hint)
        cached = self._load(key)
        if cached is not None:
            return cached
        transcript = self.inner.transcribe(audio, language=language, hint=hint)
        try:
            transcript.validate(audio.duration_ms)
        except ValidationError as exc:
            log.warning("계약을 어긴 인식 결과는 남기지 않습니다: %s", exc.message)
            return transcript
        document = {"profile": self._profile, "transcript": transcript.to_dict()}
        try:
            self._store.put(
                key, json.dumps(document, ensure_ascii=False).encode("utf-8"), "application/json"
            )
        except (StorageError, OSError) as exc:  # 남기지 못해도 인식 결과는 돌려준다
            log.warning("인식 결과를 남기지 못했습니다: %s: %s", type(exc).__name__, exc)
        return transcript

    def _key(self, audio: SpeechAudio, language: str | None, hint: str) -> str:
        digest = canonical_hash(
            {
                "audio": hashlib.sha256(audio.data).hexdigest(),
                "profile": self._profile,
                "format": TRANSCRIPT_FORMAT,
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
