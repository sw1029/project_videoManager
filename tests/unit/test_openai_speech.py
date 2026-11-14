"""OpenAI 호환 음성 인식 어댑터: 요청 형식·응답 해석·오류 처리 (가짜 전송, 네트워크·과금 없음)."""

from __future__ import annotations

import json

import pytest

from frame.adapters.analysis.openai_speech import OpenAISpeechRecognizer, dump_request_fields
from frame.adapters.http_transport import HttpResponse, HttpTransport, TransportError
from frame.adapters.llm.key_store import LLMKeyStore
from frame.application.ports.video_analysis import SpeechAudio, SpeechRecognitionError

KEY = "sk-test-SECRET-1234567890"
AUDIO = SpeechAudio(b"RIFF....WAVEfmt ", "audio/wav", 8000)

VERBOSE = {
    "task": "transcribe",
    "language": "korean",
    "duration": 7.9,
    "text": "안녕하세요 오늘의 메뉴예요. 말차 라떼입니다.",
    "segments": [
        {"id": 0, "start": 0.3, "end": 2.4, "text": " 안녕하세요 오늘의 메뉴예요."},
        {"id": 1, "start": 3.0, "end": 4.3, "text": " 말차 라떼입니다."},
    ],
    "words": [
        {"word": "안녕하세요", "start": 0.3, "end": 1.0},
        {"word": "오늘의", "start": 1.0, "end": 1.6},
        {"word": "메뉴예요", "start": 1.6, "end": 2.4},
        {"word": "말차", "start": 3.0, "end": 3.5},
        {"word": "라떼입니다", "start": 3.5, "end": 4.3},
    ],
    "usage": {"type": "duration", "seconds": 8},
}


class FakeTransport(HttpTransport):
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def request(self, method, url, *, headers=None, body=None, timeout=60.0):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": body,
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _json(document, status=200) -> HttpResponse:
    return HttpResponse(status, json.dumps(document, ensure_ascii=False).encode("utf-8"))


def _recognizer(*responses, **options) -> tuple[OpenAISpeechRecognizer, FakeTransport]:
    transport = FakeTransport(*responses)
    recognizer = OpenAISpeechRecognizer(
        key_source=LLMKeyStore(explicit=KEY, env={}), transport=transport, **options
    )
    return recognizer, transport


def test_request_asks_for_word_and_segment_timestamps():
    recognizer, transport = _recognizer(_json(VERBOSE))
    recognizer.transcribe(AUDIO, language="ko", hint="말차 라떼, 5,000원")

    sent = transport.requests[0]
    assert (sent["method"], sent["url"]) == (
        "POST",
        "https://api.openai.com/v1/audio/transcriptions",
    )
    assert sent["headers"]["Authorization"] == f"Bearer {KEY}"
    assert sent["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert dump_request_fields(sent["body"]) == {
        "model": ["whisper-1"],
        "response_format": ["verbose_json"],
        "timestamp_granularities[]": ["word", "segment"],
        "language": ["ko"],
        "prompt": ["말차 라떼, 5,000원"],
    }
    assert b'filename="audio.wav"' in sent["body"] and AUDIO.data in sent["body"]


def test_verbose_response_becomes_segments_with_words():
    recognizer, _ = _recognizer(_json(VERBOSE))
    transcript = recognizer.transcribe(AUDIO)

    assert transcript.language == "ko"
    assert [(s.start_ms, s.end_ms, s.text) for s in transcript.segments] == [
        (300, 2400, "안녕하세요 오늘의 메뉴예요."),
        (3000, 4300, "말차 라떼입니다."),
    ]
    assert [w.text for w in transcript.segments[0].words] == ["안녕하세요", "오늘의", "메뉴예요"]
    assert [(w.start_ms, w.end_ms) for w in transcript.segments[1].words] == [
        (3000, 3500),
        (3500, 4300),
    ]
    transcript.validate(AUDIO.duration_ms)
    assert recognizer.last_usage == {"type": "duration", "seconds": 8}


def test_other_base_url_and_model_use_the_same_format():
    recognizer, transport = _recognizer(
        _json({"text": "hello"}),
        model="openai/whisper-large-v3",
        base_url="https://openrouter.ai/api/v1/",
        word_timestamps=False,
    )
    transcript = recognizer.transcribe(AUDIO)

    assert transport.requests[0]["url"] == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert dump_request_fields(transport.requests[0]["body"]) == {
        "model": ["openai/whisper-large-v3"]
    }
    # 시각을 주지 않는 응답: 음성 전체가 한 구간이다
    assert [(s.start_ms, s.end_ms, s.text, s.words) for s in transcript.segments] == [
        (0, 8000, "hello", ())
    ]


def test_no_speech_is_an_empty_transcript():
    recognizer, _ = _recognizer(
        _json({"text": "", "segments": [], "words": []}), _json({"text": "  "})
    )

    assert recognizer.transcribe(AUDIO).is_empty
    assert recognizer.transcribe(AUDIO).segments == ()


def test_words_without_segments_make_one_segment():
    document = {"text": "말차 라떼입니다", "words": VERBOSE["words"][3:]}
    recognizer, _ = _recognizer(_json(document))
    transcript = recognizer.transcribe(AUDIO)

    assert [(s.start_ms, s.end_ms) for s in transcript.segments] == [(3000, 4300)]
    assert len(transcript.segments[0].words) == 2


def test_overlapping_and_overlong_segments_are_put_in_order():
    document = {
        "text": "a b c",
        "segments": [
            {"start": 3.0, "end": 5.0, "text": "b"},
            {"start": 0.0, "end": 3.4, "text": "a"},  # 뒤 구간과 겹친다
            {"start": 7.5, "end": 9.5, "text": "c"},  # 음성 길이를 넘는다
            {"start": 6.0, "end": 6.0, "text": "빈 구간"},
            {"start": "x", "end": 1.0, "text": "깨진 구간"},
        ],
    }
    recognizer, _ = _recognizer(_json(document))
    transcript = recognizer.transcribe(AUDIO)

    assert [(s.start_ms, s.end_ms, s.text) for s in transcript.segments] == [
        (0, 3400, "a"),
        (3400, 5000, "b"),
        (7500, 8000, "c"),
    ]
    transcript.validate(AUDIO.duration_ms)


def test_words_are_dropped_when_they_do_not_line_up_with_the_segments():
    """낱말이 든 구간과 들지 않은 구간이 섞이면 낱말 시각을 믿지 않는다 (구간만 쓴다)."""
    document = dict(VERBOSE, words=VERBOSE["words"][:3])
    recognizer, _ = _recognizer(_json(document))

    assert all(s.words == () for s in recognizer.transcribe(AUDIO).segments)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_json({"error": {"message": "Incorrect API key provided"}}, 401), "인증 실패"),
        (_json({"error": {"message": "Rate limit"}}, 429), "한도 초과"),
        (_json({"error": {"message": "audio file is too large"}}, 413), "too large"),
        (HttpResponse(502, b"<html>bad gateway</html>"), "502"),
        (HttpResponse(200, b"not json"), "해석하지 못했습니다"),
        (_json(["not", "an", "object"]), "JSON 객체가 아닙니다"),
        (TransportError("URLError: timed out"), "닿지 못했습니다"),
    ],
)
def test_failures_are_speech_recognition_errors_without_the_key(response, message):
    recognizer, _ = _recognizer(response)

    with pytest.raises(SpeechRecognitionError, match=message) as caught:
        recognizer.transcribe(AUDIO)
    assert KEY not in str(caught.value)


def test_missing_key_and_empty_audio_fail_before_any_request():
    transport = FakeTransport()
    recognizer = OpenAISpeechRecognizer(key_source=LLMKeyStore(env={}), transport=transport)

    with pytest.raises(SpeechRecognitionError):
        recognizer.transcribe(AUDIO)
    keyed, sent = _recognizer()
    with pytest.raises(SpeechRecognitionError, match="비어"):
        keyed.transcribe(SpeechAudio(b""))
    assert transport.requests == [] and sent.requests == []


def test_numbers_that_are_not_finite_do_not_escape_as_other_errors():
    """시각이 유한한 수가 아니면 그 낱말·구간을 건너뛰고, 길이가 수가 아니면 인식 오류다 (감사 BTS-15)."""
    document = {
        "text": "안녕하세요",
        "words": [
            {"word": "안녕", "start": 1e999, "end": 2.0},
            {"word": "하세요", "start": 0.4, "end": 0.9},
        ],
        "segments": [{"start": float("nan"), "end": 1.0, "text": "안녕하세요"}],
    }
    recognizer, _ = _recognizer(HttpResponse(200, json.dumps(document).encode("utf-8")))
    transcript = recognizer.transcribe(AUDIO)
    assert [(s.start_ms, s.end_ms) for s in transcript.segments] == [(400, 900)]

    recognizer, _ = _recognizer(_json({"text": "안녕하세요", "duration": "abc"}))
    with pytest.raises(SpeechRecognitionError):
        recognizer.transcribe(SpeechAudio(AUDIO.data, "audio/wav", None))
