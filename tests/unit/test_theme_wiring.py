"""테마·시리즈 기능의 설정과 조립 (config/theme_wiring.py)."""

from __future__ import annotations

import pytest

from frame.adapters.series.audio import FakeAudioEditor
from frame.adapters.series.overlay import FakeOverlayRenderer, FakeScreenTextReader
from frame.adapters.series.planning import TemplateSeriesPlanner
from frame.adapters.series.source_analysis import FakeMediaProber, FakeShotDetector
from frame.adapters.series.store import ArtifactThemeRepository, InMemoryThemeRepository
from frame.adapters.series.theme_extraction import FakeThemeExtractor
from frame.adapters.speech import FakeSpeechRecognizer
from frame.application.errors import ConfigurationError
from frame.config import Settings, build_container
from frame.config.theme_settings import ThemeSettings
from tests.support.factories import make_container
from tests.support.video import find_ffmpeg

FFMPEG = find_ffmpeg()


def test_defaults_are_offline_fakes():
    """기본값으로는 키·ffmpeg·글꼴 없이 서버가 뜬다."""
    series = make_container().series

    assert isinstance(series.prober, FakeMediaProber)
    assert isinstance(series.shot_detector, FakeShotDetector)
    assert isinstance(series.recognizer, FakeSpeechRecognizer)
    assert isinstance(series.extractor, FakeThemeExtractor)
    assert isinstance(series.planner, TemplateSeriesPlanner)
    assert isinstance(series.audio, FakeAudioEditor)
    assert isinstance(series.renderer, FakeOverlayRenderer)
    assert series.text_reader is None
    assert isinstance(series.themes, ArtifactThemeRepository)
    assert series.adapter_keys() == {
        "theme_media": "fake",
        "speech_recognizer": "fake",
        "theme_extractor": "fake",
        "series_planner": "template",
        "audio_editor": "fake",
        "overlay_renderer": "fake",
        "screen_text_reader": "off",
        "theme_store": "artifact",
    }


def test_default_container_still_builds_without_overrides():
    assert build_container(Settings()).series is not None


def test_settings_are_read_from_the_environment():
    env = {
        "FRAME_THEME_MEDIA": "ffmpeg",
        "FRAME_SPEECH_RECOGNIZER": "openai",
        "FRAME_SPEECH_MODEL": "openai/whisper-large-v3",
        "FRAME_SPEECH_BASE_URL": "https://openrouter.ai/api/v1",
        "FRAME_SPEECH_KEY_FILE": "keys/speech.key",
        "FRAME_THEME_EXTRACTOR": "vlm",
        "FRAME_THEME_FRAMES": "2",
        "FRAME_SERIES_PLANNER": "llm",
        "FRAME_AUDIO_EDITOR": "ffmpeg",
        "FRAME_OVERLAY_RENDERER": "ffmpeg",
        "FRAME_OVERLAY_FONT": "fonts/korean-bold.ttf",
        "FRAME_SCREEN_TEXT_READER": "vlm",
        "FRAME_THEME_STORE": "memory",
        "FRAME_SHORTFORM_PLATFORMS": "youtube_shorts",
        "FRAME_THEME_MIN_SLOT_MS": "2000",
        "FRAME_THEME_MAX_SLOT_MS": "10000",
        "FRAME_SHOT_THRESHOLD": "0.25",
    }
    settings = ThemeSettings.from_env(env)

    assert settings == ThemeSettings(
        media="ffmpeg",
        speech="openai",
        speech_model="openai/whisper-large-v3",
        speech_base_url="https://openrouter.ai/api/v1",
        speech_key_file="keys/speech.key",
        extractor="vlm",
        extractor_frames=2,
        planner="llm",
        audio="ffmpeg",
        overlay="ffmpeg",
        overlay_font="fonts/korean-bold.ttf",
        text_reader="vlm",
        store="memory",
        platforms=("YOUTUBE_SHORTS",),
        min_slot_ms=2000,
        max_slot_ms=10000,
        shot_threshold=0.25,
    )
    # .env 에 빈 값으로 남겨 둔 항목은 기본값을 쓴다
    assert (
        ThemeSettings.from_env({"FRAME_THEME_MEDIA": "", "FRAME_OVERLAY_FONT": " "})
        == ThemeSettings()
    )


def test_settings_choose_the_adapters():
    settings = ThemeSettings(store="memory", text_reader="fake", speech="off")
    series = make_container(theme_settings=settings).series

    assert isinstance(series.themes, InMemoryThemeRepository)
    assert isinstance(series.text_reader, FakeScreenTextReader)
    assert series.recognizer is None


@pytest.mark.parametrize(
    ("changes", "name"),
    [
        ({"media": "magic"}, "FRAME_THEME_MEDIA"),
        ({"speech": "magic"}, "FRAME_SPEECH_RECOGNIZER"),
        ({"extractor": "magic"}, "FRAME_THEME_EXTRACTOR"),
        ({"planner": "magic"}, "FRAME_SERIES_PLANNER"),
        ({"audio": "magic"}, "FRAME_AUDIO_EDITOR"),
        ({"overlay": "magic"}, "FRAME_OVERLAY_RENDERER"),
        ({"text_reader": "magic"}, "FRAME_SCREEN_TEXT_READER"),
        ({"store": "magic"}, "FRAME_THEME_STORE"),
        ({"platforms": ("TIKTOK",)}, "FRAME_SHORTFORM_PLATFORMS"),
    ],
)
def test_unknown_adapter_key_fails_at_startup(changes, name):
    with pytest.raises(ConfigurationError, match=name):
        make_container(theme_settings=ThemeSettings(**changes))


def test_override_wins_over_the_setting():
    spy = FakeSpeechRecognizer()
    series = make_container(
        theme_settings=ThemeSettings(speech="magic"), speech_recognizer=spy
    ).series

    assert series.recognizer is spy


def test_real_adapters_are_built_without_any_network_call():
    """실제 어댑터를 고르면 그 구현이 조립된다. LLM 은 재생 클라이언트라 호출이 나가지 않는다."""
    from frame.adapters.series.planning.llm import LLMSeriesPlanner
    from frame.adapters.speech.caching import CachingSpeechRecognizer
    from frame.adapters.speech.openai import OpenAISpeechRecognizer

    settings = Settings(jobs="recording", persistence="memory", llm_provider="fake")
    theme_settings = ThemeSettings(speech="openai", planner="llm", speech_model="whisper-1")
    series = make_container(settings, theme_settings=theme_settings).series

    # 유료 인식기에는 결과를 남기는 래퍼가 씌워진다 (같은 소리는 한 번만 과금)
    assert isinstance(series.recognizer, CachingSpeechRecognizer)
    assert isinstance(series.recognizer.inner, OpenAISpeechRecognizer)
    assert series.recognizer.inner.model == "whisper-1"
    assert isinstance(series.planner, LLMSeriesPlanner)


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
def test_ffmpeg_and_vlm_adapters_are_built_when_selected():
    from frame.adapters.series.audio.ffmpeg import FfmpegAudioEditor
    from frame.adapters.series.overlay.caching_reader import CachingScreenTextReader
    from frame.adapters.series.overlay.vlm_reader import VlmScreenTextReader
    from frame.adapters.series.source_analysis.ffmpeg import FfmpegMediaProber, FfmpegShotDetector
    from frame.adapters.series.theme_extraction.vlm import VlmThemeExtractor

    settings = Settings(jobs="recording", persistence="memory", llm_provider="fake")
    theme_settings = ThemeSettings(
        media="ffmpeg", audio="ffmpeg", extractor="vlm", text_reader="vlm", extractor_frames=2
    )
    series = make_container(settings, theme_settings=theme_settings).series

    assert isinstance(series.prober, FfmpegMediaProber)
    assert isinstance(series.shot_detector, FfmpegShotDetector)
    assert isinstance(series.audio, FfmpegAudioEditor)
    assert isinstance(series.extractor, VlmThemeExtractor) and series.extractor.frames_per_slot == 2
    assert isinstance(series.text_reader, CachingScreenTextReader)
    assert isinstance(series.text_reader.inner, VlmScreenTextReader)


def test_missing_font_fails_at_startup(tmp_path):
    if FFMPEG is None:
        pytest.skip("ffmpeg 없음 (video extra 미설치)")
    settings = ThemeSettings(overlay="ffmpeg", overlay_font=str(tmp_path / "missing.ttf"))

    with pytest.raises(ConfigurationError, match="FRAME_OVERLAY_FONT"):
        make_container(theme_settings=settings)


# ---- 음성 인식의 주소와 키 (감사 BTS-11) ----
class _SpyTransport:
    """실린 주소와 헤더만 적고 빈 받아쓰기를 돌려준다 (네트워크 없음)."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def request(self, method, url, *, headers=None, body=None, timeout=60.0):
        from frame.adapters.http_transport import HttpResponse

        self.requests.append({"url": url, "headers": dict(headers or {})})
        return HttpResponse(200, b'{"text": "", "segments": [], "words": []}')


def test_screen_reader_cache_follows_everything_that_changes_what_is_read():
    """화면 읽기의 캐시 키에는 모델뿐 아니라 주소와 읽는 방법(자르는 여백·크기·프롬프트 판)이 든다 (감사 BTS-D10).

    글자가 잘려 읽히는 것을 여백을 넓혀 고쳐도, 키가 모델뿐이면 옛 읽기가 나온다.
    """
    from frame.adapters.series.overlay import vlm_reader
    from frame.adapters.series.overlay.vlm_reader import VlmScreenTextReader

    reader = VlmScreenTextReader("ffmpeg", llm=object())
    wider = VlmScreenTextReader("ffmpeg", llm=object(), padding=0.2)
    smaller = VlmScreenTextReader("ffmpeg", llm=object(), max_side=512)

    assert vlm_reader.PROMPT_VERSION in reader.signature
    assert len({reader.signature, wider.signature, smaller.signature}) == 3
    assert reader.signature == VlmScreenTextReader("ffmpeg", llm=object()).signature


def _model_folder(parent, *, without=()):
    """받아 둔 로컬 인식 모델을 흉내 낸 폴더 (내용은 가짜다)."""
    folder = parent / "turbo"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("model.bin", "config.json", "tokenizer.json"):
        if name not in without:
            (folder / name).write_bytes(f"fake {name}".encode())
    return folder


def _local_package(monkeypatch, *, installed: bool = True) -> None:
    """faster-whisper 꾸러미가 설치돼 있는(또는 없는) 것처럼 보이게 한다."""
    import importlib.util

    found = importlib.util.find_spec
    answer = object() if installed else None
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: answer if name == "faster_whisper" else found(name),
    )


def test_local_recognizer_needs_its_optional_package(monkeypatch, tmp_path):
    """로컬 인식은 선택 설치다. 꾸러미가 없으면 조립할 때 무엇을 설치할지 알린다. 있으면 설정대로 만든다."""
    from frame.adapters.speech.caching import CachingSpeechRecognizer
    from frame.adapters.speech.faster_whisper import FasterWhisperRecognizer

    folder = _model_folder(tmp_path)
    settings = ThemeSettings(
        speech="faster_whisper", speech_local_model=str(folder), speech_local_vad=False
    )
    _local_package(monkeypatch, installed=False)
    with pytest.raises(ConfigurationError, match="speech-local"):
        make_container(theme_settings=settings)

    _local_package(monkeypatch)
    recognizer = make_container(theme_settings=settings).series.recognizer
    assert isinstance(recognizer, CachingSpeechRecognizer)
    local = recognizer.inner
    assert isinstance(local, FasterWhisperRecognizer)
    assert (local.model, local.device, local.vad) == (str(folder), "auto", False)


def test_local_recognizer_only_opens_a_model_that_is_already_downloaded(monkeypatch, tmp_path):
    """실행 중에는 모델을 내려받지 않는다: 받아 둔 폴더만 연다 (감사 BTS-D60).

    이름을 받으면 첫 받아쓰기 요청 안에서, 판을 고정하지 않은 채 1.6GB 를 받게 된다 (그 동안 요청이 멈춘다).
    폴더가 없거나 모델 파일이 빠졌으면 기동할 때 설정 오류로 알린다. 모델을 열어 보지는 않는다.
    """
    _local_package(monkeypatch)

    def build(model):
        settings = ThemeSettings(speech="faster_whisper", speech_local_model=model)
        return make_container(theme_settings=settings)

    for model, reason in (
        (None, "받아 둔 폴더"),
        ("large-v3-turbo", "폴더가 아닙니다"),  # 이름으로는 받지 않는다
        (str(tmp_path / "nowhere"), "폴더가 아닙니다"),
    ):
        with pytest.raises(ConfigurationError, match=reason) as caught:
            build(model)
        assert "FRAME_SPEECH_LOCAL_MODEL" in str(caught.value)
    broken = _model_folder(tmp_path / "broken", without=("model.bin", "tokenizer.json"))
    with pytest.raises(ConfigurationError, match=r"model\.bin.*tokenizer\.json") as caught:
        build(str(broken))
    assert str(tmp_path) not in str(caught.value)  # 오류 글에 이 컴퓨터의 경로를 싣지 않는다
    assert ThemeSettings().speech_local_model is None  # 기본값으로 받는 이름이 없다


def test_local_cache_profile_names_the_model_not_where_it_is(monkeypatch, tmp_path):
    """결과를 남기는 키에는 모델의 정체와 답을 바꾸는 설정이 든다. 폴더의 위치는 들지 않는다 (감사 BTS-D66).

    위치가 들면 그 컴퓨터의 절대 경로가 저장된 문서에 남고, 폴더를 옮기면 같은 모델인데 캐시가 빗나간다.
    """
    _local_package(monkeypatch)

    def signature(folder, **changes) -> str:
        settings = ThemeSettings(speech="faster_whisper", speech_local_model=str(folder), **changes)
        recognizer = make_container(theme_settings=settings).series.recognizer
        assert recognizer.signature == recognizer.inner.signature  # 래퍼의 키는 인식기의 정체다
        return recognizer.signature

    here, moved = _model_folder(tmp_path / "a"), _model_folder(tmp_path / "b")

    first = signature(here)
    assert first == signature(moved)  # 옮겨도 같은 모델이다
    assert str(tmp_path) not in first and "turbo" not in first
    assert first != signature(here, speech_local_compute="int8")  # 연산 방식은 답을 바꿀 수 있다
    assert first != signature(here, speech_local_vad=False)
    (moved / "model.bin").write_bytes(b"other weights")
    assert first != signature(moved)  # 다른 가중치


def test_local_recognizer_settings_come_from_the_environment():
    settings = ThemeSettings.from_env(
        {
            "FRAME_SPEECH_RECOGNIZER": "faster_whisper",
            "FRAME_SPEECH_LOCAL_MODEL": "small",
            "FRAME_SPEECH_LOCAL_DEVICE": "cpu",
            "FRAME_SPEECH_LOCAL_COMPUTE": "int8",
            "FRAME_SPEECH_LOCAL_VAD": "off",
        }
    )

    assert settings.speech == "faster_whisper"
    assert (settings.speech_local_model, settings.speech_local_device) == ("small", "cpu")
    assert settings.speech_local_compute == "int8" and settings.speech_local_vad is False
    assert ThemeSettings.from_env({}).speech_local_vad is True
    assert ThemeSettings.from_env({}).speech_local_model is None


def _recognizer(theme_settings, settings=None, **overrides):
    base = settings or Settings(jobs="recording", persistence="memory", llm_provider="fake")
    recognizer = make_container(base, theme_settings=theme_settings, **overrides).series.recognizer
    recognizer = recognizer.inner  # 결과를 남기는 래퍼 안의 실제 인식기
    spy = _SpyTransport()
    recognizer._http = spy
    return recognizer, spy


def _heard(recognizer, spy) -> dict:
    from frame.application.ports.speech import SpeechAudio

    recognizer.transcribe(SpeechAudio(b"RIFF....WAVEfmt ", "audio/wav", 1000))
    return spy.requests[-1]


def test_speech_address_follows_the_llm_address(tmp_path, monkeypatch):
    """주소를 따로 주지 않으면 인식은 LLM 과 같은 곳으로 간다 (LLM 키가 다른 주소로 가지 않는다)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
    recognizer, spy = _recognizer(ThemeSettings(speech="openai"))
    assert recognizer.base_url == "https://api.openai.com/v1"
    assert _heard(recognizer, spy)["headers"]["Authorization"] == "Bearer sk-env-key"

    local = Settings(
        jobs="recording",
        persistence="memory",
        llm_provider="fake",
        llm_base_url="http://localhost:8001/v1/",
    )
    recognizer, spy = _recognizer(ThemeSettings(speech="openai"), local)
    assert recognizer.base_url == "http://localhost:8001/v1"
    assert _heard(recognizer, spy)["url"] == "http://localhost:8001/v1/audio/transcriptions"


def test_speech_at_another_address_only_uses_its_own_key_file(tmp_path, monkeypatch):
    """인식 주소가 LLM 주소와 다르면 전용 키 파일의 키만 싣는다. 환경 변수의 OpenAI 키는 쓰지 않는다."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai-key")
    key_file = tmp_path / "speech.txt"
    key_file.write_text("sk-or-speech-key", encoding="utf-8")
    elsewhere = ThemeSettings(
        speech="openai",
        speech_base_url="https://openrouter.ai/api/v1",
        speech_key_file=str(key_file),
    )

    recognizer, spy = _recognizer(elsewhere)
    sent = _heard(recognizer, spy)

    assert sent["url"] == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert sent["headers"]["Authorization"] == "Bearer sk-or-speech-key"


def test_speech_at_another_address_without_a_key_file_is_refused_at_startup(tmp_path, monkeypatch):
    """전용 키 파일 없이 다른 주소를 쓰면 기동을 거절한다 (LLM 키·환경 변수의 키를 그 주소로 보내지 않는다)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai-key")
    elsewhere = ThemeSettings(speech="openai", speech_base_url="https://openrouter.ai/api/v1")
    with pytest.raises(ConfigurationError, match="FRAME_SPEECH_KEY_FILE"):
        _recognizer(elsewhere)

    missing = ThemeSettings(
        speech="openai",
        speech_base_url="https://openrouter.ai/api/v1",
        speech_key_file=str(tmp_path / "none.txt"),
    )
    with pytest.raises(ConfigurationError, match="FRAME_SPEECH_KEY_FILE"):
        _recognizer(missing)

    # 반대 방향: LLM 주소를 로컬로 바꿨는데 인식 주소만 OpenAI 로 적은 경우도 같다
    local = Settings(
        jobs="recording",
        persistence="memory",
        llm_provider="fake",
        llm_base_url="http://localhost:8001/v1",
    )
    openai = ThemeSettings(speech="openai", speech_base_url="https://api.openai.com/v1")
    with pytest.raises(ConfigurationError, match="FRAME_SPEECH_KEY_FILE"):
        _recognizer(openai, local)


def test_speech_address_must_be_https(tmp_path):
    key_file = tmp_path / "speech.txt"
    key_file.write_text("sk-key", encoding="utf-8")
    plain = ThemeSettings(
        speech="openai", speech_base_url="http://example.com/v1", speech_key_file=str(key_file)
    )
    with pytest.raises(ConfigurationError, match="FRAME_SPEECH_BASE_URL"):
        _recognizer(plain)

    local = ThemeSettings(
        speech="openai", speech_base_url="http://127.0.0.1:9000/v1", speech_key_file=str(key_file)
    )
    recognizer, _ = _recognizer(local)  # 로컬 시험 서버는 받는다
    assert recognizer.base_url == "http://127.0.0.1:9000/v1"


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
def test_frame_extraction_retries_are_set_apart_from_the_llm_retries():
    """프레임이 붙는 추출의 재질의 횟수는 FRAME_LLM_MAX_ATTEMPTS 를 따르지 않는다 (감사 BTS-22)."""
    settings = Settings(
        jobs="recording", persistence="memory", llm_provider="fake", llm_max_attempts=5
    )
    series = make_container(settings, theme_settings=ThemeSettings(extractor="vlm")).series
    assert series.extractor.max_attempts == 2

    custom = ThemeSettings.from_env({"FRAME_THEME_EXTRACTOR": "vlm", "FRAME_THEME_ATTEMPTS": "1"})
    assert custom.extractor_attempts == 1
    assert make_container(settings, theme_settings=custom).series.extractor.max_attempts == 1


# ---- 환경 변수는 입구에서 한 번만 읽는다 (감사 BTS-13) ----
def test_container_does_not_read_theme_settings_from_the_process_environment(monkeypatch):
    """build_container 에 설정을 직접 넘기면 셸의 FRAME_THEME_* 가 끼어들지 않는다 (테스트·도구가 격리된다)."""
    monkeypatch.setenv("FRAME_THEME_STORE", "memory")
    monkeypatch.setenv("FRAME_SPEECH_RECOGNIZER", "off")
    monkeypatch.setenv("FRAME_THEME_FRAMES", "not a number")

    series = make_container().series

    assert isinstance(series.themes, ArtifactThemeRepository)
    assert isinstance(series.recognizer, FakeSpeechRecognizer)
    assert series.settings == ThemeSettings()


def test_server_entry_reads_the_theme_settings_once(monkeypatch):
    import os

    from frame.presentation.main import create_default_app

    # 셸의 FRAME_* · 키 변수가 이 테스트에 끼어들지 않게 한다 (감사 BTS-D11: FRAME_STRUCTURER=llm 을 둔 셸에서는
    # 실패했고, 키가 있는 셸에서는 실제 클라이언트를 조립했다)
    for name in list(os.environ):
        if name.startswith("FRAME_") or name in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("FRAME_THEME_STORE", "memory")
    monkeypatch.setenv("FRAME_SPEECH_RECOGNIZER", "off")
    series = create_default_app().state.container.series

    assert isinstance(series.themes, InMemoryThemeRepository) and series.recognizer is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FRAME_THEME_FRAMES", "three"),
        ("FRAME_THEME_ATTEMPTS", "2.5"),
        ("FRAME_THEME_MIN_SLOT_MS", ""),
        ("FRAME_SHOT_THRESHOLD", "high"),
        ("FRAME_SPEECH_TIMEOUT_S", "soon"),
    ],
)
def test_setting_that_is_not_a_number_names_the_variable(name, value):
    """숫자가 아닌 설정은 변수 이름이 든 ConfigurationError 다 (이름 없는 ValueError 로 서버 기동을 막지 않는다)."""
    if not value:  # 빈 값은 기본값이다
        assert ThemeSettings.from_env({name: value}) == ThemeSettings()
        return
    with pytest.raises(ConfigurationError, match=name):
        ThemeSettings.from_env({name: value})


@pytest.mark.parametrize(
    ("env", "name"),
    [
        ({"FRAME_THEME_MIN_SLOT_MS": "0"}, "FRAME_THEME_MIN_SLOT_MS"),
        ({"FRAME_THEME_MAX_SLOT_MS": "1000"}, "FRAME_THEME_MAX_SLOT_MS"),  # 하한(3000)보다 작다
        ({"FRAME_THEME_MAX_SLOT_MS": "-5"}, "FRAME_THEME_MAX_SLOT_MS"),
        ({"FRAME_THEME_ATTEMPTS": "-3"}, "FRAME_THEME_ATTEMPTS"),
        ({"FRAME_THEME_FRAMES": "0"}, "FRAME_THEME_FRAMES"),
        ({"FRAME_SPEECH_TIMEOUT_S": "nan"}, "FRAME_SPEECH_TIMEOUT_S"),
        ({"FRAME_SPEECH_TIMEOUT_S": "0"}, "FRAME_SPEECH_TIMEOUT_S"),
        ({"FRAME_SHOT_THRESHOLD": "7"}, "FRAME_SHOT_THRESHOLD"),
        ({"FRAME_SHOT_THRESHOLD": "0"}, "FRAME_SHOT_THRESHOLD"),
    ],
)
def test_setting_out_of_its_range_is_refused_at_startup_by_name(env, name):
    """값의 범위는 기동할 때 본다 (감사 BTS-D41).

    보지 않으면 추출 도중에 — 인식을 부른 뒤에 — 이름 없는 오류로 드러난다 (상한이 하한보다 작은 경우).
    """
    with pytest.raises(ConfigurationError, match=name):
        ThemeSettings.from_env(env)
    assert ThemeSettings.from_env({"FRAME_THEME_MAX_SLOT_MS": "8000"}).max_slot_ms == 8000
    assert (
        ThemeSettings.from_env({"FRAME_THEME_MAX_SLOT_MS": "0"}).max_slot_ms is None
    )  # 0 은 '없음'


def test_settings_built_in_code_are_checked_when_the_services_are_assembled():
    with pytest.raises(ConfigurationError, match="FRAME_THEME_ATTEMPTS"):
        make_container(theme_settings=ThemeSettings(extractor_attempts=0))


def test_speech_address_that_follows_the_llm_address_says_so_when_it_is_refused():
    """인식 주소를 주지 않았으면 LLM 주소를 따른다. 그 주소가 거절될 때 사용자가 주지 않은 변수를 탓하지 않는다."""
    remote = Settings(llm_base_url="http://10.0.0.5:8001/v1")
    with pytest.raises(ConfigurationError, match="FRAME_LLM_BASE_URL") as caught:
        make_container(remote, theme_settings=ThemeSettings(speech="openai"))
    assert "https" in str(caught.value)
    given = ThemeSettings(speech="openai", speech_base_url="http://example.com/v1")
    with pytest.raises(ConfigurationError, match="FRAME_SPEECH_BASE_URL 은 https"):
        make_container(theme_settings=given)


def test_theme_store_falls_back_to_memory_when_the_artifact_store_cannot_name_uris():
    """키로 주소를 지을 수 없는 산출물 저장소에서는 테마 저장소가 메모리로 물러난다. 서버 기동을 막지 않는다."""
    from frame.application.ports.artifact_store import ArtifactStore

    class OpaqueStore(ArtifactStore):
        def put(self, key, data, content_type=None):
            return "remote://1"

        def get(self, uri):
            return b""

        def exists(self, uri):
            return False

    series = make_container(artifact_store=OpaqueStore()).series

    assert isinstance(series.themes, InMemoryThemeRepository)
    assert type(series.episodes).__name__ == "InMemoryEpisodeRepository"
