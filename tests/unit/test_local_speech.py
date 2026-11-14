"""로컬 음성 인식 어댑터 (FRAME_SPEECH_RECOGNIZER=faster_whisper, ADR-0028). 가짜 모델로 돌린다 (모델·GPU 없음)."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from frame.adapters.speech.faster_whisper import FasterWhisperRecognizer
from frame.application.ports.speech import (
    SpeechAudio,
    SpeechRecognitionError,
    SpeechServiceError,
)
from frame.config.theme_settings import ThemeSettings

AUDIO = SpeechAudio(b"RIFF....WAVEfmt ", "audio/wav", 16000)


def _word(start: float, end: float, text: str) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end, word=text, probability=0.9)


def _segment(start, end, text, words=(), logprob=-0.2, no_speech=0.01) -> SimpleNamespace:
    return SimpleNamespace(
        start=start,
        end=end,
        text=text,
        words=list(words),
        avg_logprob=logprob,
        no_speech_prob=no_speech,
    )


class _Model:
    """faster-whisper 의 WhisperModel 흉내: transcribe 가 (구간들, 정보) 를 돌려준다."""

    def __init__(self, segments=(), language="ko", error: Exception | None = None) -> None:
        self.segments, self.language, self.error = list(segments), language, error
        self.calls: list[dict] = []

    def transcribe(self, source, **options):
        self.calls.append({"bytes": len(source), **options})
        if self.error is not None:
            raise self.error
        return iter(self.segments), SimpleNamespace(language=self.language)


def _recognizer(model: _Model, **changes) -> FasterWhisperRecognizer:
    defaults = ThemeSettings()
    options = {
        "model": defaults.speech_local_model,
        "device": defaults.speech_local_device,
        "compute_type": defaults.speech_local_compute,
        "vad": defaults.speech_local_vad,
        **changes,
    }
    options.setdefault("decoder", lambda data, rate: data)  # 소리를 풀지 않고 그대로 넘긴다
    return FasterWhisperRecognizer(loader=lambda device: model, **options)


def test_segments_words_and_confidence_become_a_transcript():
    model = _Model(
        [
            _segment(
                0.52,
                2.62,
                " 부드러운 시그니처 라떼입니다.",
                [
                    _word(0.52, 1.1, " 부드러운"),
                    _word(1.1, 1.9, " 시그니처"),
                    _word(1.9, 2.62, " 라떼입니다."),
                ],
                logprob=-0.25,
                no_speech=0.02,
            ),
            _segment(8.9, 10.7, "가격은 4,500원이에요."),
        ]
    )

    transcript = _recognizer(model).transcribe(AUDIO, language="ko", hint="시그니처 라떼")

    first, second = transcript.segments
    assert transcript.language == "ko"
    assert (first.start_ms, first.end_ms, first.text) == (
        520,
        2620,
        "부드러운 시그니처 라떼입니다.",
    )
    assert [(w.start_ms, w.end_ms, w.text) for w in first.words] == [
        (520, 1100, "부드러운"),
        (1100, 1900, "시그니처"),
        (1900, 2620, "라떼입니다."),
    ]
    assert first.confidence == pytest.approx(math.exp(-0.25)) and first.no_speech == 0.02
    assert second.words == () and second.text == "가격은 4,500원이에요."
    transcript.validate(AUDIO.duration_ms)


def test_the_model_gets_the_language_the_hint_and_voice_detection():
    model = _Model()

    _recognizer(model).transcribe(AUDIO, language="ko", hint="  시그니처 라떼  ")
    _recognizer(model, vad=False).transcribe(AUDIO)

    first, second = model.calls
    assert first["bytes"] == len(AUDIO.data)
    assert first["language"] == "ko" and first["initial_prompt"] == "시그니처 라떼"
    assert first["word_timestamps"] is True and first["vad_filter"] is True
    assert first["condition_on_previous_text"] is False
    assert second["language"] is None and second["initial_prompt"] is None
    assert second["vad_filter"] is False


def test_no_speech_is_an_empty_transcript_and_empty_audio_is_an_error():
    recognizer = _recognizer(_Model())

    assert recognizer.transcribe(AUDIO, language="ko").is_empty
    with pytest.raises(SpeechRecognitionError):
        recognizer.transcribe(SpeechAudio(b"", "audio/wav", 0))


def test_sound_that_cannot_be_decoded_is_a_recognition_error_not_a_service_error():
    """읽을 수 없는 소리(형식·깨진 파일)는 내용의 문제다. 모델을 돌리기 전에 소리를 풀다가 드러난다."""

    def decoder(data: bytes, rate: int):
        raise ValueError("Invalid data found when processing input")

    model = _Model([_segment(0.0, 1.0, "안녕하세요")])
    recognizer = _recognizer(model, decoder=decoder)

    with pytest.raises(SpeechRecognitionError, match="읽지 못했습니다") as caught:
        recognizer.transcribe(AUDIO)
    assert not isinstance(caught.value, SpeechServiceError) and model.calls == []


@pytest.mark.parametrize(
    "error",
    [
        MemoryError("Unable to allocate 2.1 GiB"),
        ValueError("'xx' is not a valid language code"),
        RuntimeError("CTranslate2: out of memory"),  # GPU 라이브러리 문제가 아닌 실행 오류
        OSError("vad model is missing"),
    ],
)
def test_trouble_while_transcribing_is_a_service_error_not_unreadable_sound(error):
    """받아 적는 중의 오류는 환경·설정의 문제다 ('알아듣지 못함' 이 아니다 — 감사 BTS-D64).

    '알아듣지 못함' 으로 올리면 테마 추출이 내레이션 없이 유료 추출로 넘어간다.
    """
    recognizer = _recognizer(_Model(error=error), device="cpu")

    with pytest.raises(SpeechServiceError, match="받아 적지 못했습니다"):
        recognizer.transcribe(AUDIO)


def test_failure_after_falling_back_to_the_cpu_is_a_service_error():
    """GPU 라이브러리가 없어 CPU 로 바꾼 뒤의 오류도 포트의 오류 종류로 올라온다 (감사 BTS-D32)."""
    devices: list[str] = []

    def loader(device: str):
        devices.append(device)
        return _Model(
            error=RuntimeError("Library cublas64_12.dll is not found")
            if device != "cpu"
            else RuntimeError("out of memory")
        )

    recognizer = FasterWhisperRecognizer(
        model="large-v3-turbo",
        device="auto",
        compute_type="default",
        vad=True,
        loader=loader,
        decoder=lambda data, rate: data,
    )

    with pytest.raises(SpeechServiceError, match="받아 적지 못했습니다"):
        recognizer.transcribe(AUDIO)
    assert devices == ["auto", "cpu"]


def test_segments_are_put_in_order_inside_the_sound():
    """모델이 준 시각을 그대로 쓰지 않는다: 순서·겹침·소리 길이를 포트의 계약에 맞춘다 (감사 BTS-D61)."""
    model = _Model(
        [
            _segment(1.5, 4.25, "둘째", [_word(1.5, 2.5, "둘"), _word(2.5, 4.25, "째")]),
            _segment(0.5, 2.0, "첫째"),
        ]
    )

    transcript = _recognizer(model).transcribe(SpeechAudio(b"RIFF", "audio/wav", 4000))

    transcript.validate(4000)
    assert [(s.start_ms, s.end_ms, s.text) for s in transcript.segments] == [
        (500, 2000, "첫째"),
        (2000, 4000, "둘째"),
    ]


def test_a_missing_gpu_library_falls_back_to_the_cpu_once():
    """GPU 는 보이는데 cuBLAS·cuDNN 이 없으면 받아 적을 때 오류가 난다. CPU 로 바꿔 다시 한다."""
    broken = _Model(error=RuntimeError("Library cublas64_12.dll is not found or cannot be loaded"))
    working = _Model([_segment(0.0, 1.0, "안녕하세요")])
    devices: list[str] = []

    def loader(device: str):
        devices.append(device)
        return broken if device != "cpu" else working

    recognizer = FasterWhisperRecognizer(
        model="large-v3-turbo",
        device="auto",
        compute_type="default",
        vad=True,
        loader=loader,
        decoder=lambda data, rate: data,
    )

    assert recognizer.transcribe(AUDIO).text == "안녕하세요"
    assert devices == ["auto", "cpu"] and recognizer.device == "cpu"
    assert recognizer.transcribe(AUDIO).text == "안녕하세요" and devices == ["auto", "cpu"]


def test_a_model_that_cannot_be_loaded_is_a_service_error():
    def loader(device: str):
        raise OSError("model.bin not found")

    recognizer = FasterWhisperRecognizer(
        model="missing", device="cpu", compute_type="default", vad=True, loader=loader
    )

    with pytest.raises(SpeechServiceError, match="모델을 열지 못했습니다"):
        recognizer.transcribe(AUDIO)
