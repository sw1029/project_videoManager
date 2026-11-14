"""로컬 음성 인식 어댑터 (FRAME_SPEECH_RECOGNIZER=faster_whisper, ADR-0028).

faster-whisper (Whisper 를 CTranslate2 로 돌린다) 로 이 컴퓨터에서 받아 적는다. 과금이 없고 소리가 밖으로 나가지 않는다.
- 선택 설치다: pip install -e ".[speech-local]". 꾸러미가 없으면 조립할 때 설정 오류로 알린다
  (config/registrations/speech.py).
- 모델·장치·연산 방식·말 구간 검출은 설정이 정한다 (FRAME_SPEECH_LOCAL_*, config/theme_settings.py). 여기에는 기본값을
  두지 않는다.
- 모델은 받아 둔 폴더에서만 연다 (local_files_only). 실행 중에 허브에 묻거나 내려받지 않는다: 이름으로 받으면
  첫 요청 안에서, 판을 고정하지 않은 채 1.6GB 를 받게 된다 (감사 BTS-D60). 받는 방법과 잰 판(저장소·revision·sha256)은
  ADR-0028 결정 9 에 있다. 조립이 기동 때 폴더와 파일(REQUIRED_MODEL_FILES)을 확인한다.
- 정체(signature)는 모델 파일의 지문과 답을 바꾸는 설정이다. 폴더의 위치는 넣지 않는다 (감사 BTS-D66).
- 말 구간 검출 (꾸러미에 든 Silero VAD): 말이 있는 곳만 받아 적는다. 끄면 말이 없는 소리에서 문장을 지어내고
  ('다음 영상에서 만나요.'), 발화를 통째로 놓치기도 한다 (오픈 데이터 457발화 가운데 3개. 켜면 0).
- 구간마다 낱말 시각, 신뢰도(평균 로그 확률), 말이 아닐 가능성을 준다 — OpenAI 호환 어댑터와 같은 것을 채운다.
- 포트의 계약은 여기서 맞춘다: 모델이 준 구간을 시간 순서로 놓고, 겹침을 없애고, 소리 길이 안으로 자른다
  (domain/speech.ordered_segments — OpenAI 호환 어댑터와 같은 함수. 감사 BTS-D61).
- 오류의 종류 (감사 BTS-D64)
  - 소리를 읽지 못하면(형식을 모르거나 깨진 파일) SpeechRecognitionError: 소리의 문제다.
  - 모델을 열지 못하거나 받아 적는 중에 난 오류(메모리·연산 라이브러리·잘못된 언어 코드)는 SpeechServiceError:
    환경·설정의 문제다. 부른 쪽은 그 오류를 넘기고 다음 유료 단계로 가지 않는다.
  어느 단계의 오류인지가 종류를 정한다. 그래서 소리는 모델에 넘기기 전에 여기서 먼저 푼다.
- GPU 가 보여도 cuBLAS·cuDNN 이 없으면 받아 적을 때 오류가 난다. 그때는 CPU 로 바꿔 한 번 다시 한다.
- 모델은 처음 받아 적을 때 연다 (서버가 뜰 때 몇 초씩 걸리지 않게).
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from frame.application.ports.speech import (
    SpeechAudio,
    SpeechRecognitionError,
    SpeechRecognizer,
    SpeechServiceError,
)
from frame.domain.speech import SpeechSegment, Transcript, Word, ordered_segments

log = logging.getLogger(__name__)
CPU = "cpu"
_GPU_LIBRARY_HINTS = (
    "cublas",
    "cudnn",
    "cuda",
)  # 이 낱말이 든 오류는 GPU 라이브러리를 찾지 못한 것이다
_WHISPER_SAMPLE_RATE = 16_000  # Whisper 가 듣는 표본화율 (Hz). 모델이 알려 주지 않을 때만 쓴다
# 이 파일이 없으면 모델을 열 수 없거나 (가중치·설정), 꾸러미가 허브에서 받으려 한다 (토크나이저)
REQUIRED_MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")
# 모델의 정체에 드는 파일 (있는 것만). 폴더의 다른 파일(README 등)은 보지 않는다
_MODEL_FILES = (
    *REQUIRED_MODEL_FILES,
    "vocabulary.json",
    "vocabulary.txt",
    "preprocessor_config.json",
)
_FINGERPRINT_SAMPLE_BYTES = 1024 * 1024  # 큰 파일은 앞뒤에서 이만큼씩만 읽어 지문에 넣는다


def missing_model_files(folder: Path) -> tuple[str, ...]:
    """받아 둔 모델 폴더에 없는 필수 파일의 이름. 모델을 열지는 않는다."""
    return tuple(name for name in REQUIRED_MODEL_FILES if not (folder / name).is_file())


def model_fingerprint(folder: Path) -> str:
    """받아 둔 모델의 정체: 모델 파일의 이름·크기·내용에서 낸 지문 (16진 16자). 폴더의 위치는 들지 않는다.

    가중치는 크므로 (large-v3-turbo 1.6GB) 큰 파일은 앞뒤 1MiB 씩만 읽는다: 다른 가중치는 거기서도 다르다.
    전체 해시가 아니다. 받은 파일이 문서에 적힌 판과 같은지는 sha256 으로 따로 확인한다 (ADR-0028 결정 9).
    """
    digest = hashlib.sha256()
    for name in _MODEL_FILES:
        path = folder / name
        if not path.is_file():
            continue
        size = path.stat().st_size
        digest.update(f"{name}:{size}:".encode())
        with path.open("rb") as handle:
            if size <= 2 * _FINGERPRINT_SAMPLE_BYTES:
                digest.update(handle.read())
            else:
                digest.update(handle.read(_FINGERPRINT_SAMPLE_BYTES))
                handle.seek(size - _FINGERPRINT_SAMPLE_BYTES)
                digest.update(handle.read(_FINGERPRINT_SAMPLE_BYTES))
    return digest.hexdigest()[:16]


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
        decoder: Callable[[bytes, int], Any] | None = None,
    ) -> None:
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self.vad = vad
        self.beam_size = beam_size
        self._loader = loader or self._load
        self._decoder = decoder or _decode  # (소리, 표본화율) -> 모델이 듣는 표본
        self._loaded: Any = None
        self._lock = threading.Lock()

    @property
    def signature(self) -> str:
        vad = "on" if self.vad else "off"
        return (
            f"faster-whisper:{model_fingerprint(Path(self.model))}"
            f":compute={self.compute_type}:beam={self.beam_size}:vad={vad}"
        )

    def transcribe(
        self, audio: SpeechAudio, *, language: str | None = None, hint: str = ""
    ) -> Transcript:
        if not audio.data:
            raise SpeechRecognitionError("인식할 음성이 비어 있습니다")
        samples = self._samples(audio.data)
        try:
            heard, found = self._run(samples, language, hint)
        except RuntimeError as exc:
            if self.device == CPU or not _gpu_library_missing(exc):
                raise _service_error(exc) from exc
            log.warning("GPU 로 받아 적지 못해 CPU 로 바꿉니다: %s", exc)
            with self._lock:
                self.device, self._loaded = CPU, None
            try:
                heard, found = self._run(samples, language, hint)
            except RuntimeError as again:  # CPU 로 바꾼 뒤의 오류도 포트의 오류 종류로 올린다
                raise _service_error(again) from again
        return Transcript(found or language, ordered_segments(heard, audio.duration_ms))

    def _samples(self, data: bytes) -> Any:
        """소리를 모델이 듣는 꼴로 푼다. 읽지 못하면 소리의 문제다 (SpeechRecognitionError)."""
        rate = _sampling_rate(self._model())
        try:
            return self._decoder(data, rate)
        except MemoryError as exc:
            raise _service_error(exc) from exc
        except Exception as exc:  # 형식을 모르거나 깨진 파일
            raise SpeechRecognitionError(f"소리를 읽지 못했습니다: {exc}") from exc

    def _run(
        self, samples: Any, language: str | None, hint: str
    ) -> tuple[list[SpeechSegment], str | None]:
        """(받아 적은 구간, 알아낸 언어). RuntimeError 는 그대로 올린다 (GPU 라이브러리 문제일 수 있다)."""
        model = self._model()
        try:
            segments, info = model.transcribe(
                samples,
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
        except Exception as exc:  # 메모리 · 잘못된 언어 코드 · 말 구간 검출 모델을 읽지 못함
            raise _service_error(exc) from exc
        return [s for s in heard if s is not None], getattr(info, "language", None)

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
        # 받아 둔 폴더만 연다. local_files_only 가 없으면 꾸러미가 허브에 묻는다 (이름이면 내려받는다)
        return _package().WhisperModel(
            self.model, device=device, compute_type=self.compute_type, local_files_only=True
        )


def _package() -> Any:
    """faster-whisper 꾸러미. 선택 설치라 쓸 때 가져온다 (없으면 조립할 때 설정 오류로 알린다)."""
    import faster_whisper

    return faster_whisper


def _decode(data: bytes, sampling_rate: int) -> Any:
    return _package().decode_audio(io.BytesIO(data), sampling_rate=sampling_rate)


def _sampling_rate(model: Any) -> int:
    extractor = getattr(model, "feature_extractor", None)
    return int(getattr(extractor, "sampling_rate", None) or _WHISPER_SAMPLE_RATE)


def _gpu_library_missing(exc: BaseException) -> bool:
    return any(hint in str(exc).lower() for hint in _GPU_LIBRARY_HINTS)


def _service_error(exc: BaseException) -> SpeechServiceError:
    return SpeechServiceError(f"로컬 인식기가 받아 적지 못했습니다 ({type(exc).__name__}): {exc}")


def _segment(raw: Any) -> SpeechSegment | None:
    """모델이 준 구간 하나. 글이 없거나 시각을 쓸 수 없으면 None (건너뛴다)."""
    text = " ".join(str(raw.text or "").split())
    try:
        start, end = _ms(raw.start), _ms(raw.end)
    except (TypeError, ValueError, OverflowError):
        return None
    if not text or end <= start:
        return None
    return SpeechSegment(
        start,
        end,
        text,
        tuple(_words(getattr(raw, "words", None) or ())),
        confidence=_confidence(raw.avg_logprob),
        no_speech=_unit(raw.no_speech_prob),
    )


def _words(raw: Any) -> list[Word]:
    words: list[Word] = []
    for word in raw:
        text = str(word.word).strip()
        try:
            start, end = _ms(word.start), _ms(word.end)
        except (TypeError, ValueError, OverflowError):
            continue
        if text:
            words.append(Word(start, max(start, end), text))
    return words


def _ms(seconds: Any) -> int:
    """초 -> 밀리초. 수가 아니거나 유한하지 않으면 TypeError·ValueError·OverflowError."""
    return max(0, round(float(seconds) * 1000))


def _confidence(avg_logprob: Any) -> float | None:
    """구간의 평균 로그 확률 -> 0..1 의 신뢰도. 수가 아니면 None."""
    try:
        return _unit(math.exp(float(avg_logprob)))
    except (TypeError, ValueError, OverflowError):
        return None


def _unit(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return min(1.0, max(0.0, number)) if math.isfinite(number) else None
