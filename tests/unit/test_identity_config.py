"""FRAME_IDENTITY · FRAME_VLM_MODEL 설정과 컨테이너 조립 (ADR-0015)."""

from __future__ import annotations

import pytest

from frame.adapters.identity import FrameIdentityExtractor, VlmCropIdentityExtractor
from frame.adapters.llm import ReplayLLMClient
from frame.application.errors import ConfigurationError
from frame.config import Settings, build_container


@pytest.fixture
def fake_ffmpeg(tmp_path):
    """실행하지 않는 자리 표시 파일 (조립 단계는 경로가 있는지만 본다)."""
    path = tmp_path / "ffmpeg.exe"
    path.write_bytes(b"")
    return path


def test_frame_extractor_is_built_with_the_configured_ffmpeg(fake_ffmpeg):
    container = build_container(Settings(identity="frame", video_ffmpeg=str(fake_ffmpeg)))
    assert isinstance(container.identity_extractor, FrameIdentityExtractor)


def test_vlm_crop_extractor_uses_the_llm_provider(fake_ffmpeg):
    settings = Settings(identity="vlm_crop", llm_provider="fake", video_ffmpeg=str(fake_ffmpeg))
    extractor = build_container(settings).identity_extractor

    assert isinstance(extractor, VlmCropIdentityExtractor)
    assert isinstance(extractor._llm, ReplayLLMClient)


def test_vlm_model_overrides_the_structuring_model(fake_ffmpeg, tmp_path):
    pytest.importorskip("openai")
    key_file = tmp_path / "key.txt"
    key_file.write_text("sk-test-not-a-real-key", encoding="utf-8")
    base = {
        "identity": "vlm_crop",
        "video_ffmpeg": str(fake_ffmpeg),
        "llm_key_file": str(key_file),
        "llm_model": "gpt-4.1-mini",
    }

    default = build_container(Settings(**base)).identity_extractor
    custom = build_container(Settings(**base, vlm_model="gpt-4.1")).identity_extractor

    assert default._llm.model == "gpt-4.1-mini"
    assert custom._llm.model == "gpt-4.1"


def test_vlm_model_is_read_from_the_environment():
    assert Settings.from_env({"FRAME_VLM_MODEL": "gpt-4.1"}).vlm_model == "gpt-4.1"
    assert Settings.from_env({"FRAME_VLM_MODEL": ""}).vlm_model is None


def test_video_reading_extractors_fail_at_startup_without_ffmpeg(tmp_path):
    missing = str(tmp_path / "no-ffmpeg.exe")
    for identity in ("frame", "vlm_crop"):
        settings = Settings(identity=identity, llm_provider="fake", video_ffmpeg=missing)
        with pytest.raises(ConfigurationError, match="ffmpeg"):
            build_container(settings)


def test_unknown_identity_key_fails_at_startup():
    with pytest.raises(ConfigurationError, match="IdentityReferenceExtractor"):
        build_container(Settings(identity="telepathy"))
