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


def test_attribute_check_of_the_vlm_crop_extractor_can_be_turned_off(fake_ffmpeg):
    base = {"identity": "vlm_crop", "llm_provider": "fake", "video_ffmpeg": str(fake_ffmpeg)}

    assert build_container(Settings(**base)).identity_extractor.verify_attributes is True
    off = build_container(Settings(**base, identity_verify_attributes=False))
    assert off.identity_extractor.verify_attributes is False


def test_attribute_check_setting_is_read_from_the_environment():
    assert Settings.from_env({}).identity_verify_attributes is True
    off = Settings.from_env({"FRAME_IDENTITY_VERIFY_ATTRIBUTES": "false"})
    assert off.identity_verify_attributes is False


def test_video_reading_extractors_fail_at_startup_without_ffmpeg(tmp_path):
    missing = str(tmp_path / "no-ffmpeg.exe")
    for identity in ("frame", "vlm_crop"):
        settings = Settings(identity=identity, llm_provider="fake", video_ffmpeg=missing)
        with pytest.raises(ConfigurationError, match="ffmpeg"):
            build_container(settings)


def test_unknown_identity_key_fails_at_startup():
    with pytest.raises(ConfigurationError, match="IdentityReferenceExtractor"):
        build_container(Settings(identity="telepathy"))


# ---- 실제 검수기 (FRAME_CHECKERS) ------------------------------------------------------


def test_real_checkers_are_built_from_their_keys(fake_ffmpeg):
    from frame.adapters.checker import SsimPreservationChecker, VlmConsistencyChecker

    settings = Settings(
        checkers=("fake_requirement", "vlm_consistency", "ssim_preservation"),
        llm_provider="fake",
        video_ffmpeg=str(fake_ffmpeg),
        consistency_frames=5,
        consistency_threshold=0.8,
        preservation_threshold=0.7,
    )
    _, consistency, preservation = build_container(settings).checkers

    assert isinstance(consistency, VlmConsistencyChecker)
    assert (consistency.frames_per_scene, consistency.threshold) == (5, 0.8)
    assert isinstance(preservation, SsimPreservationChecker) and preservation.threshold == 0.7


def test_checker_settings_are_read_from_the_environment():
    settings = Settings.from_env(
        {
            "FRAME_CHECKERS": "fake_requirement, vlm_consistency ,ssim_preservation",
            "FRAME_CONSISTENCY_FRAMES": "4",
            "FRAME_CONSISTENCY_THRESHOLD": "0.85",
            "FRAME_PRESERVATION_THRESHOLD": "0.6",
        }
    )
    assert settings.checkers == ("fake_requirement", "vlm_consistency", "ssim_preservation")
    assert settings.consistency_frames == 4
    assert (settings.consistency_threshold, settings.preservation_threshold) == (0.85, 0.6)
    defaults = Settings.from_env({})
    assert (defaults.consistency_frames, defaults.consistency_threshold) == (3, 0.9)


def test_real_checkers_fail_at_startup_without_ffmpeg(tmp_path):
    settings = Settings(
        checkers=("ssim_preservation",), video_ffmpeg=str(tmp_path / "no-ffmpeg.exe")
    )
    with pytest.raises(ConfigurationError, match="ffmpeg"):
        build_container(settings)


def test_requirement_and_keep_checkers_are_built_from_their_keys(fake_ffmpeg):
    """실제 요구 반영·유지 요소 검수기 (ADR-0018)."""
    from frame.adapters.checker import VlmPreservationChecker, VlmRequirementChecker

    settings = Settings(
        checkers=("vlm_requirement", "vlm_preservation"),
        llm_provider="fake",
        video_ffmpeg=str(fake_ffmpeg),
        requirement_frames=4,
        requirement_threshold=0.75,
        preservation_frames=3,
    )
    requirement, preservation = build_container(settings).checkers

    assert isinstance(requirement, VlmRequirementChecker)
    assert (requirement.frames_per_scene, requirement.threshold) == (4, 0.75)
    assert isinstance(preservation, VlmPreservationChecker) and preservation.frames_per_scene == 3


def test_requirement_and_keep_checker_settings_are_read_from_the_environment():
    settings = Settings.from_env(
        {
            "FRAME_REQUIREMENT_FRAMES": "2",
            "FRAME_REQUIREMENT_THRESHOLD": "0.8",
            "FRAME_PRESERVATION_FRAMES": "1",
        }
    )
    assert (settings.requirement_frames, settings.requirement_threshold) == (2, 0.8)
    assert settings.preservation_frames == 1
    defaults = Settings.from_env({})
    assert (defaults.requirement_frames, defaults.requirement_threshold) == (3, 0.5)
    assert defaults.preservation_frames == 2
