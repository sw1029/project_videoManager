"""음성 인식 (SpeechRecognizer) 의 등록 — 말소리를 낱말 시각이 붙은 받아쓰기로 옮긴다 (ADR-0026, 0028).

어느 키를 쓸지는 테마·시리즈 설정의 FRAME_SPEECH_RECOGNIZER 가 정한다:
fake · openai (OpenAI 호환 API) · faster_whisper (이 컴퓨터에서) · off (받아 적지 않는다).
- 실제 구현은 팩토리가 불릴 때 import 한다. 로컬 인식의 꾸러미(faster-whisper)는 선택 설치다.
- 필요한 것이 없으면 기동 시점에 실패한다. 인식 API 의 키만 예외다: 화면에서 넣는 키가 있어 기동 때 알 수 없다
  (전용 키 파일은 기동 때 확인한다).
"""

from __future__ import annotations

from pathlib import Path

from frame.adapters.speech import FakeSpeechRecognizer
from frame.application.errors import ConfigurationError
from frame.application.ports.llm import LLMKeySource
from frame.application.ports.speech import SpeechRecognizer

from ..registry import AdapterRegistry, BuildContext
from ._dependencies import artifact_store, llm_address, theme_settings


def register(registry: AdapterRegistry) -> None:
    registry.register(SpeechRecognizer, "fake", lambda ctx: FakeSpeechRecognizer())
    registry.register(SpeechRecognizer, "openai", _openai)
    registry.register(SpeechRecognizer, "faster_whisper", _local)
    registry.register(SpeechRecognizer, "off", lambda ctx: None)


def _openai(ctx: BuildContext) -> SpeechRecognizer:
    from frame.adapters.llm.key_store import LLMKeyStore
    from frame.adapters.speech.caching import CachingSpeechRecognizer
    from frame.adapters.speech.openai import OpenAISpeechRecognizer

    settings, ts = ctx.settings, theme_settings(ctx)
    llm_base = llm_address(ctx)
    base_url = _speech_base_url(ts.speech_base_url or llm_base, given=bool(ts.speech_base_url))
    key_source: LLMKeySource
    if base_url == llm_base and ts.speech_key_file is None:
        # LLM 과 같은 주소: LLM 이 쓰는 키를 함께 쓴다 (화면에서 넣은 키 포함)
        key_source = ctx.llm_keys or LLMKeyStore(key_file=settings.llm_key_file)
    else:
        # 다른 주소이거나 전용 키를 줬다: 그 키 파일의 키만 싣는다. 환경 변수(OPENAI_API_KEY)나 LLM 키로
        # 대신하지 않는다. LLMKeyStore 는 파일보다 환경 변수를 먼저 보므로 환경 변수 대체를 끈다 (감사 BTS-11)
        if ts.speech_key_file is None:
            raise ConfigurationError(
                f"FRAME_SPEECH_KEY_FILE 이 필요합니다: 인식 주소({base_url})가 LLM 주소({llm_base})와 "
                "다릅니다. LLM 키를 다른 주소로 보내지 않도록 인식 전용 키 파일을 주세요"
            )
        if not Path(ts.speech_key_file).is_file():
            raise ConfigurationError("FRAME_SPEECH_KEY_FILE: 키 파일이 없습니다")
        key_source = LLMKeyStore(key_file=ts.speech_key_file, env={})
    recognizer = OpenAISpeechRecognizer(
        key_source=key_source,
        model=ts.speech_model,
        base_url=base_url,
        timeout_s=ts.speech_timeout_s,
    )
    # 같은 소리는 한 번만 과금한다: 결과를 산출물 저장소에 남긴다 (감사 BTS-12). 키는 인식기의 정체(모델@주소)다
    return CachingSpeechRecognizer(recognizer, artifact_store(ctx))


def _speech_base_url(url: str, *, given: bool = True) -> str:
    """인식 API 주소는 https 만 받는다 (로컬 시험 서버는 예외). 키를 평문으로 보내지 않기 위해서다.

    given 이 거짓이면 인식 주소를 주지 않아 LLM 주소를 따른 것이다. 거절할 때 그렇게 알린다
    (사용자가 주지 않은 변수의 이름만 대면 무엇을 고칠지 알 수 없다 — 감사 BTS-D13).

    판정은 OpenRouter 어댑터와 같은 것을 쓴다 (adapters/shared/http 의 secure_base_url. 감사 BTS-11).
    """
    from frame.adapters.shared.http import secure_base_url

    checked = secure_base_url(url)
    if checked is not None:
        return checked
    if given:
        raise ConfigurationError(
            f"FRAME_SPEECH_BASE_URL 은 https 여야 합니다 (키를 평문으로 보내지 않도록): {url}"
        )
    raise ConfigurationError(
        f"음성 인식 주소가 https 가 아닙니다: {url}. FRAME_SPEECH_BASE_URL 을 주지 않아 LLM 주소"
        "(FRAME_LLM_BASE_URL)를 따랐습니다. 인식 API 는 https 만 받습니다 (키를 평문으로 보내지 "
        "않도록): FRAME_SPEECH_BASE_URL 로 https 주소를 주거나 다른 인식기를 고르세요"
    )


def _local(ctx: BuildContext) -> SpeechRecognizer:
    # 부를 때 찾는다: 꾸러미가 있는지는 조립하는 그때의 환경으로 본다
    from importlib.util import find_spec

    from frame.adapters.speech.caching import CachingSpeechRecognizer
    from frame.adapters.speech.faster_whisper import FasterWhisperRecognizer

    ts = theme_settings(ctx)
    if find_spec("faster_whisper") is None:
        raise ConfigurationError(
            "FRAME_SPEECH_RECOGNIZER=faster_whisper 에 필요한 faster-whisper 꾸러미가 없습니다. "
            'pip install -e ".[speech-local]" 로 설치하세요'
        )
    local = FasterWhisperRecognizer(
        model=str(_local_model_folder(ts.speech_local_model)),
        device=ts.speech_local_device,
        compute_type=ts.speech_local_compute,
        vad=ts.speech_local_vad,
    )
    # 과금은 없지만 같은 소리를 다시 받아 적지 않는다 (시간이 든다). 키는 모델의 지문과 답을 바꾸는 설정이다
    return CachingSpeechRecognizer(local, artifact_store(ctx))


def _local_model_folder(setting: str | None) -> Path:
    """받아 둔 로컬 인식 모델의 폴더. 없거나 모델 파일이 빠졌으면 기동 때 알린다 (감사 BTS-D60).

    실행 중에는 모델을 내려받지 않는다. 모델을 열어 보지는 않는다 (몇 초가 걸린다. 처음 받아 적을 때 연다).
    오류 글에는 폴더의 경로를 싣지 않는다.
    """
    from frame.adapters.speech.faster_whisper import missing_model_files

    how = "받는 방법은 docs/adr/0028-script-extraction-and-evidence.md 결정 9 에 있습니다"
    if not setting:
        raise ConfigurationError(
            "FRAME_SPEECH_LOCAL_MODEL: 로컬 인식 모델을 받아 둔 폴더를 주세요. "
            f"실행 중에는 모델을 내려받지 않습니다 ({how})"
        )
    folder = Path(setting)
    if not folder.is_dir():
        raise ConfigurationError(
            "FRAME_SPEECH_LOCAL_MODEL: 폴더가 아닙니다. 모델 이름으로는 받지 않습니다. "
            f"모델을 받아 둔 폴더의 경로를 주세요 ({how})"
        )
    missing = missing_model_files(folder)
    if missing:
        raise ConfigurationError(
            f"FRAME_SPEECH_LOCAL_MODEL: 폴더에 모델 파일이 없습니다: {', '.join(missing)} ({how})"
        )
    return folder
