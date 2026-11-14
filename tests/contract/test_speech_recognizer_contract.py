"""음성 인식 포트의 계약 테스트 (ADR-0025·0026): SpeechRecognizer.

Fake, OpenAI 호환 구현, 로컬 구현(faster-whisper)이 같은 규약을 지킨다.
실제 구현은 가짜 전송·가짜 모델로 돌린다 (네트워크·과금·모델 파일 없음).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from frame.adapters.fake_media import fake_recording
from frame.adapters.http_transport import HttpResponse, HttpTransport
from frame.adapters.llm.key_store import LLMKeyStore
from frame.adapters.speech import FakeSpeechRecognizer
from frame.adapters.speech.faster_whisper import FasterWhisperRecognizer
from frame.adapters.speech.openai import OpenAISpeechRecognizer
from frame.application.ports.speech import SpeechAudio, SpeechRecognitionError
from frame.config.theme_settings import ThemeSettings
from frame.domain.speech import SpeechSegment, Transcript
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


class _CannedModel:
    """faster-whisper 모델 흉내: 정해 둔 구간을 돌려주거나 오류를 낸다. 말이 없는 소리(b"RIFF-silence")에는 빈 결과."""

    def __init__(self, error: Exception | None = None, segments=SAID.segments) -> None:
        self._error = error
        self._segments = segments

    def transcribe(self, source, **options):
        if self._error is not None:
            raise self._error
        heard = [] if source == b"RIFF-silence" else self._segments
        segments = [
            SimpleNamespace(
                start=s.start_ms / 1000,
                end=s.end_ms / 1000,
                text=s.text,
                words=[],
                avg_logprob=-0.2,
                no_speech_prob=0.01,
            )
            for s in heard
        ]
        return iter(segments), SimpleNamespace(language="ko")


def _local_recognizer(
    error: Exception | None = None, segments=SAID.segments
) -> FasterWhisperRecognizer:
    defaults = ThemeSettings()
    return FasterWhisperRecognizer(
        model=defaults.speech_local_model,
        device=defaults.speech_local_device,
        compute_type=defaults.speech_local_compute,
        vad=defaults.speech_local_vad,
        loader=lambda device: _CannedModel(error, segments),
        decoder=lambda data, rate: (
            data
        ),  # 소리를 풀지 않고 그대로 넘긴다 (가짜 모델이 바이트를 본다)
    )


@pytest.fixture(params=["fake", "openai", "local"])
def recognizer_case(request):
    """(인식기, 말이 든 음성, 말이 없는 음성, 실패하는 인식기)."""
    if request.param == "local":
        return (
            _local_recognizer(),
            SpeechAudio(b"RIFF-speech", "audio/wav", 4000),
            SpeechAudio(b"RIFF-silence", "audio/wav", 2000),
            _local_recognizer(RuntimeError("CTranslate2 failed")),
        )
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


# 엔진이 준 그대로의 구간: 순서가 바뀌고, 겹치고, 소리 길이(4000 ms)를 넘는다
MESSY = (
    SpeechSegment(1500, 4250, "둘째 줄입니다."),
    SpeechSegment(500, 2000, "첫째 줄입니다."),
)


@pytest.mark.parametrize("engine", ["openai", "local"])
def test_segments_the_engine_gives_out_of_line_still_meet_the_contract(engine):
    """계약은 어댑터가 지킨다: 시간 순서, 겹치지 않음, 소리 길이 안 (감사 BTS-D61).

    엔진이 끝을 1 ms 라도 넘겨 주면 받아쓰기 검사에서 거절되고, 테마 추출은 그것을 '알아듣지 못함' 으로 보아
    내레이션 없이 유료 추출로 넘어간다. 받아 적는 데는 성공했는데 대본이 버려진다.
    """
    audio = SpeechAudio(b"RIFF-speech", "audio/wav", 4000)
    if engine == "local":
        recognizer = _local_recognizer(segments=MESSY)
    else:
        document = {
            "language": "korean",
            "text": " ".join(s.text for s in MESSY),
            "segments": [
                {"start": s.start_ms / 1000, "end": s.end_ms / 1000, "text": s.text} for s in MESSY
            ],
        }
        recognizer = _openai_recognizer(200, document)

    transcript = recognizer.transcribe(audio, language="ko")

    transcript.validate(audio.duration_ms)
    assert [(s.start_ms, s.end_ms, s.text) for s in transcript.segments] == [
        (500, 2000, "첫째 줄입니다."),
        (2000, 4000, "둘째 줄입니다."),
    ]


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
