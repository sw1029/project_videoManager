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
    from frame.adapters.analysis.openai_speech import OpenAISpeechRecognizer
    from frame.adapters.series.llm import LLMSeriesPlanner

    settings = Settings(jobs="recording", persistence="memory", llm_provider="fake")
    theme_settings = ThemeSettings(speech="openai", planner="llm", speech_model="whisper-1")
    series = make_container(settings, theme_settings=theme_settings).series

    assert isinstance(series.recognizer, OpenAISpeechRecognizer)
    assert series.recognizer.model == "whisper-1"
    assert isinstance(series.planner, LLMSeriesPlanner)


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
def test_ffmpeg_and_vlm_adapters_are_built_when_selected():
    from frame.adapters.analysis.ffmpeg_media import FfmpegMediaProber, FfmpegShotDetector
    from frame.adapters.analysis.vlm_theme import VlmThemeExtractor
    from frame.adapters.audio.ffmpeg import FfmpegAudioEditor
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
    assert isinstance(series.text_reader, VlmScreenTextReader)


def test_missing_font_fails_at_startup(tmp_path):
    if FFMPEG is None:
        pytest.skip("ffmpeg 없음 (video extra 미설치)")
    settings = ThemeSettings(overlay="ffmpeg", overlay_font=str(tmp_path / "missing.ttf"))

    with pytest.raises(ConfigurationError, match="FRAME_OVERLAY_FONT"):
        make_container(theme_settings=settings)
