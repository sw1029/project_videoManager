"""OpenAI 호환 음성 인식 API 어댑터 (FRAME_SPEECH_RECOGNIZER=openai, ADR-0025·0026).

POST {base_url}/audio/transcriptions (multipart). OpenAI, OpenRouter, 로컬 Whisper 서버가 같은 형식을 쓴다.
- 모델·주소·시간 제한은 설정이 정한다 (FRAME_SPEECH_MODEL 등, config/theme_settings.py). 여기에는 기본값을 두지 않는다.
  설정의 기본 모델은 whisper-1 이다: 2026-11 기준 OpenAI 에서 낱말 시각을 주는 모델이 이것뿐이다
  (response_format=verbose_json + timestamp_granularities[]=word,segment). 낱말 시각이 있어야 쉬지 않고 이어 읽은
  녹음을 대본 줄 사이에서 나눌 수 있다.
- 구간·낱말 시각을 주지 않는 모델(글만 돌려주는 모델)도 받는다: 음성 전체를 한 구간으로 본다.
  이때는 편 전체를 읽은 녹음을 줄에 맞출 수 없으니 줄마다 녹음해야 한다.
- 키는 Authorization 헤더에만 싣는다. 예외·로그·반환값에 남기지 않는다.
- 과금 단위(초)는 last_usage 에 남긴다 (whisper-1: usage.type=duration).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from typing import Any

from frame.adapters.http_transport import (
    HttpResponse,
    HttpTransport,
    TransportError,
    UrllibTransport,
)
from frame.application.ports.llm import LLMKeySource
from frame.application.ports.speech import (
    SpeechAudio,
    SpeechRecognitionError,
    SpeechRecognizer,
    SpeechServiceError,
)
from frame.domain.speech import SpeechSegment, Transcript, Word

_LANGUAGE_CODES = {"korean": "ko", "english": "en", "japanese": "ja", "chinese": "zh"}
_SUFFIX = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
}


class OpenAISpeechRecognizer(SpeechRecognizer):
    def __init__(
        self,
        *,
        key_source: LLMKeySource,
        model: str,
        base_url: str,
        timeout_s: float,
        word_timestamps: bool = True,
        transport: HttpTransport | None = None,
    ) -> None:
        self._keys = key_source
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.word_timestamps = word_timestamps
        self._http = transport or UrllibTransport()
        self.last_usage: Mapping[str, Any] | None = None  # 마지막 호출의 과금 단위 (기록용)

    def transcribe(
        self, audio: SpeechAudio, *, language: str | None = None, hint: str = ""
    ) -> Transcript:
        if not audio.data:
            raise SpeechRecognitionError("인식할 음성이 비어 있습니다")
        key = self._keys.resolve()
        if not key:
            raise SpeechServiceError(self._keys.describe_missing())
        fields: list[tuple[str, str]] = [("model", self.model)]
        if self.word_timestamps:
            fields += [
                ("response_format", "verbose_json"),
                ("timestamp_granularities[]", "word"),
                ("timestamp_granularities[]", "segment"),
            ]
        if language:
            fields.append(("language", language))
        if hint.strip():
            fields.append(("prompt", hint.strip()))
        suffix = _SUFFIX.get(audio.media_type.lower(), "wav")
        body, content_type = _multipart(fields, f"audio.{suffix}", audio.media_type, audio.data)
        try:
            response = self._http.request(
                "POST",
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": content_type},
                body=body,
                timeout=self.timeout_s,
            )
        except TransportError as exc:
            raise SpeechServiceError(f"음성 인식 API 에 닿지 못했습니다: {exc}") from exc
        document = self._document(response)
        self.last_usage = document.get("usage") if isinstance(document.get("usage"), dict) else None
        return _transcript(document, language, audio.duration_ms)

    def _document(self, response: HttpResponse) -> dict[str, Any]:
        if response.status in (401, 403):
            raise SpeechServiceError(
                f"음성 인식 API 인증 실패 ({response.status}): API 키를 확인하세요"
            )
        if response.status == 429:
            raise SpeechServiceError("음성 인식 API 요청 한도 초과 (429)")
        if response.status >= 500:
            raise SpeechServiceError(f"음성 인식 API 서버 오류 ({response.status})")
        if response.status >= 400:
            raise SpeechRecognitionError(
                f"음성 인식 API 오류 {response.status}: {_error_message(response)}"
            )
        try:
            document = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise SpeechRecognitionError("음성 인식 API 응답을 해석하지 못했습니다") from exc
        if not isinstance(document, dict):
            raise SpeechRecognitionError("음성 인식 API 응답이 JSON 객체가 아닙니다")
        return document


def _multipart(
    fields: list[tuple[str, str]], filename: str, media_type: str, data: bytes
) -> tuple[bytes, str]:
    boundary = f"frame-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {media_type}\r\n\r\n"
        ).encode()
    )
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _error_message(response: HttpResponse) -> str:
    """API 가 돌려준 오류 설명 (키가 들어갈 일은 없지만 길이를 자른다)."""
    try:
        document = response.json()
        error = document.get("error") if isinstance(document, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        if message:
            return str(message)[:300].replace("\n", " ")
    except (ValueError, UnicodeDecodeError):
        pass
    return response.body[:200].decode("utf-8", errors="replace").replace("\n", " ")


def _ms(value: Any) -> int:
    """초 -> 밀리초. 유한한 수가 아니면 ValueError (inf 는 round 에서 OverflowError 가 난다)."""
    seconds = float(value)
    if not math.isfinite(seconds):
        raise ValueError(f"유한한 수가 아닙니다: {value!r}")
    return max(0, round(seconds * 1000))


def _transcript(
    document: Mapping[str, Any], language: str | None, duration_ms: int | None
) -> Transcript:
    raw_language = str(document.get("language") or language or "").strip().lower() or None
    code = _LANGUAGE_CODES.get(raw_language, raw_language) if raw_language else None
    words = _words(document.get("words"))
    segments = _segments(document.get("segments"), words)
    text = " ".join(str(document.get("text") or "").split())
    if not segments and words:
        segments = [SpeechSegment(words[0].start_ms, words[-1].end_ms, text, tuple(words))]
    if not segments and text:
        # 시각을 주지 않는 모델: 음성 전체를 한 구간으로 본다
        try:
            end = duration_ms if duration_ms else _ms(document.get("duration") or 0)
        except (TypeError, ValueError) as exc:
            raise SpeechRecognitionError("음성 인식 API 응답의 길이를 해석하지 못했습니다") from exc
        segments = [SpeechSegment(0, max(end, 1), text)]
    return Transcript(code, tuple(_ordered(segments, duration_ms)))


def _words(raw: Any) -> list[Word]:
    words: list[Word] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("word") or item.get("text") or "").strip()
        try:
            start, end = _ms(item["start"]), _ms(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if text:
            words.append(Word(start, max(start, end), text))
    return sorted(words, key=lambda w: (w.start_ms, w.end_ms))


def _segments(raw: Any, words: list[Word]) -> list[SpeechSegment]:
    segments: list[SpeechSegment] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        try:
            start, end = _ms(item["start"]), _ms(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or end <= start:
            continue
        inside = tuple(w for w in words if start <= (w.start_ms + w.end_ms) / 2 < end)
        segments.append(SpeechSegment(start, end, text, inside))
    if segments and words and any(not s.words for s in segments):
        # 낱말이 구간에 고르게 들지 않으면(경계가 어긋남) 낱말 시각을 믿을 수 없다: 구간만 쓴다
        segments = [SpeechSegment(s.start_ms, s.end_ms, s.text) for s in segments]
    return segments


def _ordered(segments: list[SpeechSegment], duration_ms: int | None) -> list[SpeechSegment]:
    """시간 순서로 놓고 겹침을 없앤다 (인식기가 구간 끝을 조금 넘겨 주는 일이 있다)."""
    ordered: list[SpeechSegment] = []
    previous_end = 0
    for segment in sorted(segments, key=lambda s: (s.start_ms, s.end_ms)):
        start = max(segment.start_ms, previous_end)
        end = segment.end_ms if duration_ms is None else min(segment.end_ms, duration_ms)
        if end <= start:
            continue
        words = tuple(w for w in segment.words if w.start_ms >= start and w.end_ms <= end)
        ordered.append(
            SpeechSegment(
                start, end, segment.text, words if len(words) == len(segment.words) else ()
            )
        )
        previous_end = end
    return ordered
