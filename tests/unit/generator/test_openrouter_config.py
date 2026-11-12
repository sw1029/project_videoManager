"""FRAME_GENERATOR=external_api · FRAME_ASSEMBLER=ffmpeg 설정.

환경 변수 해석, 컨테이너 조립, 키·ffmpeg 가 없을 때 기동 시점 실패를 확인한다.
"""

from __future__ import annotations

import pytest

from frame.adapters.generator import CachingVideoGenerator
from frame.adapters.generator.ffmpeg import FfmpegVideoAssembler
from frame.adapters.generator.openrouter import OpenRouterVideoGenerator
from frame.application.errors import ConfigurationError
from frame.config import Settings, build_container

OPENROUTER_KEY = "sk-or-v1-" + "beef" * 16


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "openrouter.key"
    path.write_text(OPENROUTER_KEY + "\n", encoding="utf-8")
    return path


@pytest.fixture
def fake_ffmpeg(tmp_path):
    """기동 시에는 실행 파일 위치만 확인한다 (실행은 생성할 때)."""
    path = tmp_path / "ffmpeg.exe"
    path.write_bytes(b"")
    return path


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def test_video_settings_are_parsed_from_env():
    settings = Settings.from_env(
        {
            "FRAME_GENERATOR": "external_api",
            "FRAME_VIDEO_MODEL": "google/gemini-omni-1.2-flash",
            "FRAME_VIDEO_RESOLUTION": "480p",
            "FRAME_VIDEO_ASPECT_RATIO": "9:16",
            "FRAME_VIDEO_FPS": "24",
            "FRAME_VIDEO_KEY_FILE": "F:/openrouter.key",
            "FRAME_VIDEO_BASE_URL": "http://127.0.0.1:9000/api/v1",
            "FRAME_VIDEO_TIMEOUT_S": "900",
            "FRAME_VIDEO_POLL_INTERVAL_S": "15",
            "FRAME_VIDEO_FFMPEG": "C:/tools/ffmpeg.exe",
        }
    )
    assert settings.generator == "external_api"
    assert settings.video_model == "google/gemini-omni-1.2-flash"
    assert settings.video_resolution == "480p"
    assert settings.video_aspect_ratio == "9:16"
    assert settings.video_fps == 24
    assert settings.video_key_file == "F:/openrouter.key"
    assert settings.video_base_url == "http://127.0.0.1:9000/api/v1"
    assert settings.video_timeout_s == 900.0
    assert settings.video_poll_interval_s == 15.0
    assert settings.video_ffmpeg == "C:/tools/ffmpeg.exe"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {
            f"FRAME_VIDEO_{k}": ""
            for k in (
                "MODEL",
                "RESOLUTION",
                "ASPECT_RATIO",
                "FPS",
                "KEY_FILE",
                "BASE_URL",
                "TIMEOUT_S",
                "POLL_INTERVAL_S",
                "FFMPEG",
            )
        },
    ],
    ids=["unset", "blank"],
)
def test_video_settings_defaults(env):
    """.env.example 처럼 빈 값으로 남겨 둔 항목도 기본값이 된다."""
    s = Settings.from_env(env)
    assert (s.video_model, s.video_resolution, s.video_aspect_ratio, s.video_fps) == (
        "alibaba/wan-3.0",
        "720p",
        "9:16",
        30,
    )
    assert s.video_key_file is None and s.video_ffmpeg is None
    assert s.video_base_url == "https://openrouter.ai/api/v1"
    assert (s.video_timeout_s, s.video_poll_interval_s) == (600.0, 20.0)


def test_external_api_builds_the_openrouter_generator(key_file, fake_ffmpeg):
    settings = Settings(
        generator="external_api",
        video_key_file=str(key_file),
        video_ffmpeg=str(fake_ffmpeg),
        video_resolution="480p",
        generator_cache=False,
    )
    generator = build_container(settings).generator
    assert isinstance(generator, OpenRouterVideoGenerator)
    assert generator.model == "alibaba/wan-3.0"
    assert generator.resolution == "480p"
    assert generator.output_size == (480, 854)
    assert generator.key_hint == "..." + OPENROUTER_KEY[-4:]


def test_external_api_is_cached_by_default_under_its_own_profile(key_file, fake_ffmpeg):
    """#7: 캐시는 기본으로 켜지고, 키 프로필은 OpenRouter 설정(모델·해상도 등)을 따른다."""
    settings = Settings(
        generator="external_api",
        video_key_file=str(key_file),
        video_ffmpeg=str(fake_ffmpeg),
        video_resolution="480p",
    )
    generator = build_container(settings).generator
    assert isinstance(generator, CachingVideoGenerator)
    assert generator.profile()["generator"] == "openrouter"
    assert generator.profile()["resolution"] == "480p"


def test_external_api_reads_the_key_from_the_environment(monkeypatch, fake_ffmpeg):
    monkeypatch.setenv("OPENROUTER_API_KEY", OPENROUTER_KEY)
    settings = Settings(
        generator="external_api", video_ffmpeg=str(fake_ffmpeg), generator_cache=False
    )
    assert isinstance(build_container(settings).generator, OpenRouterVideoGenerator)


def test_external_api_without_a_key_fails_at_startup(tmp_path, fake_ffmpeg):
    settings = Settings(
        generator="external_api",
        video_key_file=str(tmp_path / "missing.key"),
        video_ffmpeg=str(fake_ffmpeg),
    )
    with pytest.raises(ConfigurationError, match="키"):
        build_container(settings)


def test_external_api_without_ffmpeg_fails_at_startup(key_file, tmp_path):
    settings = Settings(
        generator="external_api",
        video_key_file=str(key_file),
        video_ffmpeg=str(tmp_path / "no-ffmpeg.exe"),
    )
    with pytest.raises(ConfigurationError, match="ffmpeg"):
        build_container(settings)


def test_ffmpeg_assembler_is_built_with_the_same_clip_spec(fake_ffmpeg):
    settings = Settings(assembler="ffmpeg", video_ffmpeg=str(fake_ffmpeg), video_resolution="480p")
    assembler = build_container(settings).assembler
    assert isinstance(assembler, FfmpegVideoAssembler)
    assert (assembler.fps, assembler.width, assembler.height) == (30, 480, 854)


def test_ffmpeg_assembler_without_ffmpeg_fails_at_startup(tmp_path):
    settings = Settings(assembler="ffmpeg", video_ffmpeg=str(tmp_path / "no-ffmpeg.exe"))
    with pytest.raises(ConfigurationError, match="ffmpeg"):
        build_container(settings)
