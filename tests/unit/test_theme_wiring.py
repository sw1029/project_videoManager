"""테마·시리즈 기능의 설정과 조립 (config/theme_wiring.py)."""

from __future__ import annotations

import pytest

from frame.adapters.analysis import (
    FakeMediaProber,
    FakeShotDetector,
    FakeSpeechRecognizer,
    FakeThemeExtractor,
)
from frame.adapters.audio import FakeAudioEditor
from frame.adapters.compositor import FakeOverlayRenderer, FakeScreenTextReader
from frame.adapters.series import TemplateSeriesPlanner
from frame.adapters.theme_store import ArtifactThemeRepository, InMemoryThemeRepository
from frame.application.errors import ConfigurationError
from frame.config import Settings, build_container
from frame.config.theme_wiring import ThemeSettings
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
        "FRAME_SPEECH_KEY_FILE": "F:/open.txt",
        "FRAME_THEME_EXTRACTOR": "vlm",
        "FRAME_THEME_FRAMES": "2",
        "FRAME_SERIES_PLANNER": "llm",
        "FRAME_AUDIO_EDITOR": "ffmpeg",
        "FRAME_OVERLAY_RENDERER": "ffmpeg",
        "FRAME_OVERLAY_FONT": "C:/Windows/Fonts/malgunbd.ttf",
        "FRAME_SCREEN_TEXT_READER": "vlm",
        "FRAME_THEME_STORE": "memory",
        "FRAME_SHORTFORM_PLATFORMS": "youtube_shorts",
        "FRAME_THEME_MIN_SLOT_MS": "2000",
        "FRAME_SHOT_THRESHOLD": "0.25",
    }
    settings = ThemeSettings.from_env(env)

    assert settings == ThemeSettings(
        media="ffmpeg",
        speech="openai",
        speech_model="openai/whisper-large-v3",
        speech_base_url="https://openrouter.ai/api/v1",
        speech_key_file="F:/open.txt",
        extractor="vlm",
        extractor_frames=2,
        planner="llm",
        audio="ffmpeg",
        overlay="ffmpeg",
        overlay_font="C:/Windows/Fonts/malgunbd.ttf",
        text_reader="vlm",
        store="memory",
        platforms=("YOUTUBE_SHORTS",),
        min_slot_ms=2000,
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
    from frame.adapters.analysis.caching import CachingSpeechRecognizer
    from frame.adapters.analysis.openai_speech import OpenAISpeechRecognizer
    from frame.adapters.series.llm import LLMSeriesPlanner

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
    from frame.adapters.analysis.ffmpeg_media import FfmpegMediaProber, FfmpegShotDetector
    from frame.adapters.analysis.vlm_theme import VlmThemeExtractor
    from frame.adapters.audio.ffmpeg import FfmpegAudioEditor
    from frame.adapters.compositor.caching import CachingScreenTextReader
    from frame.adapters.compositor.vlm_reader import VlmScreenTextReader

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


def _recognizer(theme_settings, settings=None, **overrides):
    base = settings or Settings(jobs="recording", persistence="memory", llm_provider="fake")
    recognizer = make_container(base, theme_settings=theme_settings, **overrides).series.recognizer
    recognizer = recognizer.inner  # 결과를 남기는 래퍼 안의 실제 인식기
    spy = _SpyTransport()
    recognizer._http = spy
    return recognizer, spy


def _heard(recognizer, spy) -> dict:
    from frame.application.ports.video_analysis import SpeechAudio

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
    from frame.presentation.main import create_default_app

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
