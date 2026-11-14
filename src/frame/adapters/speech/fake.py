"""테스트·오프라인용 음성 인식. 가짜 음성(fake_recording·fake_video)에 적힌 말소리를 그대로 돌려준다.

그 밖의 바이트는 '말이 없음' 이다. error 를 주면 그 오류를 던지고, calls 에 호출을 남긴다.
"""

from __future__ import annotations

from typing import Any

from frame.adapters.fake_media import decode_fake_media
from frame.application.ports.speech import SpeechAudio, SpeechRecognizer
from frame.domain.series.source_analysis import Transcript


class FakeSpeechRecognizer(SpeechRecognizer):
    """가짜 음성에 적힌 말소리를 그대로 돌려준다. 그 밖의 바이트는 '말이 없음' 이다."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def transcribe(
        self, audio: SpeechAudio, *, language: str | None = None, hint: str = ""
    ) -> Transcript:
        self.calls.append({"bytes": len(audio.data), "language": language, "hint": hint})
        if self._error is not None:
            raise self._error
        media = decode_fake_media(audio.data)
        part = media.get("audio") if media else None
        if not isinstance(part, dict) or not isinstance(part.get("transcript"), dict):
            return Transcript(language=language)
        transcript = Transcript.from_dict(part["transcript"])
        return Transcript(transcript.language or language, transcript.segments)
