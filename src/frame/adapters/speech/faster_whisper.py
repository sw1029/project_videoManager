"""로컬 음성 인식 어댑터 (FRAME_SPEECH_RECOGNIZER=faster_whisper, ADR-0028).

faster-whisper (Whisper 를 CTranslate2 로 돌린다) 로 이 컴퓨터에서 받아 적는다. 과금이 없고 소리가 밖으로 나가지 않는다.
- 선택 설치다: pip install -e ".[speech-local]". 꾸러미가 없으면 조립할 때 설정 오류로 알린다 (config/theme_adapters).
- 모델·장치·연산 방식·말 구간 검출은 설정이 정한다 (FRAME_SPEECH_LOCAL_*, config/theme_settings.py). 여기에는 기본값을
  두지 않는다. 모델은 이름(처음 쓸 때 받는다)이거나 받아 둔 폴더의 경로다.
- 말 구간 검출 (꾸러미에 든 Silero VAD): 말이 있는 곳만 받아 적는다. 끄면 말이 없는 소리에서 문장을 지어내고
  ('다음 영상에서 만나요.'), 발화를 통째로 놓치기도 한다 (오픈 데이터 457발화 가운데 3개. 켜면 0).
- 구간마다 낱말 시각, 신뢰도(평균 로그 확률), 말이 아닐 가능성을 준다 — OpenAI 호환 어댑터와 같은 것을 채운다.
- GPU 가 보여도 cuBLAS·cuDNN 이 없으면 받아 적을 때 오류가 난다. 그때는 CPU 로 바꿔 한 번 다시 한다.
- 모델은 처음 받아 적을 때 연다 (서버가 뜰 때 몇 초씩 걸리지 않게).
"""

from __future__ import annotations

import io
import logging
import math
import threading
from collections.abc import Callable
from typing import Any

from frame.application.ports.speech import (
    SpeechAudio,
    SpeechRecognitionError,
    SpeechRecognizer,
    SpeechServiceError,
)
from frame.domain.speech import SpeechSegment, Transcript, Word

log = logging.getLogger(__name__)
CPU = "cpu"
_GPU_LIBRARY_HINTS = (
    "cublas",
    "cudnn",
    "cuda",
)  # 이 낱말이 든 오류는 GPU 라이브러리를 찾지 못한 것이다


class FasterWhisperRecognizer(SpeechRecognizer):
    def __init__(
        self,
        *,
        model: str,
        device: str,
        compute_type: str,
        vad: bool,
        beam_size: int = 5,
        loader: Callable[[str], Any] | None = None,
    ) -> None:
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self.vad = vad
        self.beam_size = beam_size
        self._loader = loader or self._load
        self._loaded: Any = None
        self._lock = threading.Lock()

    def transcribe(
        self, audio: SpeechAudio, *, language: str | None = None, hint: str = ""
    ) -> Transcript:
        if not audio.data:
            raise SpeechRecognitionError("인식할 음성이 비어 있습니다")
        try:
            return self._run(audio, language, hint)
        except RuntimeError as exc:
            if self.device == CPU or not any(k in str(exc).lower() for k in _GPU_LIBRARY_HINTS):
                raise SpeechRecognitionError(f"소리를 받아 적지 못했습니다: {exc}") from exc
            log.warning("GPU 로 받아 적지 못해 CPU 로 바꿉니다: %s", exc)
            with self._lock:
                self.device, self._loaded = CPU, None
            return self._run(audio, language, hint)

    def _run(self, audio: SpeechAudio, language: str | None, hint: str) -> Transcript:
        model = self._model()
        try:
            segments, info = model.transcribe(
                io.BytesIO(audio.data),
                language=language,
                initial_prompt=hint.strip() or None,
                beam_size=self.beam_size,
                word_timestamps=True,
                vad_filter=self.vad,
                condition_on_previous_text=False,
            )
            heard = [_segment(segment) for segment in segments]  # 구간은 여기서 실제로 받아 적힌다
        except RuntimeError:
            raise
        except Exception as exc:  # 읽을 수 없는 소리 (형식·깨진 파일)
            raise SpeechRecognitionError(f"소리를 받아 적지 못했습니다: {exc}") from exc
        found = getattr(info, "language", None) or language
        return Transcript(found, tuple(segment for segment in heard if segment is not None))

    def _model(self) -> Any:
        with self._lock:
            if self._loaded is None:
                try:
                    self._loaded = self._loader(self.device)
                except Exception as exc:
                    raise SpeechServiceError(
                        f"로컬 인식 모델을 열지 못했습니다 ({self.model}): {exc}"
                    ) from exc
            return self._loaded

    def _load(self, device: str) -> Any:
        from faster_whisper import WhisperModel

        return WhisperModel(self.model, device=device, compute_type=self.compute_type)


def _segment(raw: Any) -> SpeechSegment | None:
    text = " ".join(str(raw.text or "").split())
    start, end = _ms(raw.start), _ms(raw.end)
    if not text or end <= start:
        return None
    words = tuple(
        Word(_ms(word.start), max(_ms(word.start), _ms(word.end)), str(word.word).strip())
        for word in (getattr(raw, "words", None) or ())
        if str(word.word).strip()
    )
    return SpeechSegment(
        start,
        end,
        text,
        words,
        confidence=_unit(math.exp(float(raw.avg_logprob))),
        no_speech=_unit(float(raw.no_speech_prob)),
    )


def _ms(seconds: float) -> int:
    return max(0, round(float(seconds) * 1000))


def _unit(value: float) -> float | None:
    return min(1.0, max(0.0, value)) if math.isfinite(value) else None
