"""음성 인식 포트의 계약 테스트 (ADR-0025·0026): SpeechRecognizer.

Fake 와 OpenAI 호환 구현이 같은 규약을 지킨다. 실제 구현은 가짜 전송으로 돌린다 (네트워크·과금 없음).
"""

from __future__ import annotations

import json

import pytest

from frame.adapters.fake_media import fake_recording
from frame.adapters.http_transport import HttpResponse, HttpTransport
from frame.adapters.llm.key_store import LLMKeyStore
from frame.adapters.speech import FakeSpeechRecognizer
from frame.adapters.speech.openai import OpenAISpeechRecognizer
from frame.application.ports.speech import SpeechAudio, SpeechRecognitionError
from frame.config.theme_settings import ThemeSettings
from frame.domain.speech import Transcript
from tests.support.speech import SAID


# ---- SpeechRecognizer ----
class _CannedTransport(HttpTransport):
    """음성 인식 API 흉내: 넘겨받은 응답을 그대로 돌려준다."""

    def __init__(self, status: int, document: dict) -> None:
        self._response = HttpResponse(status, json.dumps(document, ensure_ascii=False).encode())

    def request(self, method, url, *, headers=None, body=None, timeout=60.0):
        return self._response


def _openai_recognizer(status: int, document: dict) -> OpenAISpeechRecognizer:
    keys = LLMKeyStore(explicit="sk-test", env={})
    defaults = ThemeSettings()
    return OpenAISpeechRecognizer(
        key_source=keys,
        model=defaults.speech_model,
        base_url="https://speech.test/v1",
        timeout_s=defaults.speech_timeout_s,
        transport=_CannedTransport(status, document),
    )


@pytest.fixture(params=["fake", "openai"])
def recognizer_case(request):
    """(인식기, 말이 든 음성, 말이 없는 음성, 실패하는 인식기)."""
    if request.param == "openai":
        said = {
            "language": "korean",
            "text": SAID.text,
            "segments": [
                {"start": s.start_ms / 1000, "end": s.end_ms / 1000, "text": s.text}
                for s in SAID.segments
            ],
        }
        return (
            _openai_recognizer(200, said),
            SpeechAudio(b"RIFF-speech", "audio/wav", 4000),
            SpeechAudio(b"RIFF-silence", "audio/wav", 2000),
            _openai_recognizer(500, {"error": {"message": "server error"}}),
        )
    recording = fake_recording(SAID, 4000)
    silent = fake_recording(Transcript(), 2000)
    return (
        FakeSpeechRecognizer(),
        SpeechAudio(recording, "application/json", 4000),
        SpeechAudio(silent, "application/json", 2000),
        FakeSpeechRecognizer(error=SpeechRecognitionError("주입된 오류")),
    )


def test_transcript_is_ordered_and_inside_the_audio(recognizer_case):
    recognizer, audio, _, _ = recognizer_case
    transcript = recognizer.transcribe(audio, language="ko", hint="바닐라 라떼")

    transcript.validate(audio.duration_ms)
    assert transcript.text == SAID.text and transcript.language == "ko"
    assert not transcript.is_empty and transcript.pace_cps is not None


def test_silence_is_an_empty_transcript_not_an_error(recognizer_case):
    recognizer, _, silent, _ = recognizer_case
    if isinstance(recognizer, OpenAISpeechRecognizer):  # 말이 없을 때의 API 응답
        recognizer = _openai_recognizer(200, {"text": "", "segments": []})
    transcript = recognizer.transcribe(silent)

    assert transcript.is_empty and transcript.segments == ()


def test_recognizer_failure_is_a_speech_recognition_error(recognizer_case):
    _, audio, _, failing = recognizer_case
    with pytest.raises(SpeechRecognitionError):
        failing.transcribe(audio)
