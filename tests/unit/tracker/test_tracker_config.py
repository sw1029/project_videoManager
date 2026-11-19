"""FRAME_TRACKER=opencv 의 설정과 조립 (ADR-0036)."""

from __future__ import annotations

import pytest

from frame.application.errors import ConfigurationError
from frame.config import Settings, build_container


def test_tracker_settings_are_read_from_the_environment():
    defaults = Settings.from_env({})
    assert (defaults.tracker, defaults.tracker_algorithm) == ("fake", "csrt")
    assert (defaults.tracker_sample_ms, defaults.tracker_redetect_ms) == (100, 1000)
    assert defaults.tracker_verify_boxes is True

    custom = Settings.from_env(
        {
            "FRAME_TRACKER": "opencv",
            "FRAME_TRACKER_ALGORITHM": " KCF ",
            "FRAME_TRACKER_SAMPLE_MS": "50",
            "FRAME_TRACKER_REDETECT_MS": "0",
            "FRAME_TRACKER_VERIFY_BOXES": "false",
        }
    )
    assert (custom.tracker, custom.tracker_algorithm) == ("opencv", "kcf")
    assert (custom.tracker_sample_ms, custom.tracker_redetect_ms) == (50, 0)
    assert custom.tracker_verify_boxes is False


def test_opencv_tracker_is_built_with_the_settings_and_the_vlm():
    pytest.importorskip("cv2")
    from frame.adapters.llm import ReplayLLMClient  # noqa: PLC0415
    from frame.adapters.tracker import OpenCvObjectTracker  # noqa: PLC0415

    settings = Settings(
        tracker="opencv",
        tracker_algorithm="kcf",
        tracker_sample_ms=50,
        tracker_redetect_ms=500,
        tracker_verify_boxes=False,
        llm_provider="fake",
    )
    container = build_container(settings)
    tracker = container.run_generation_pipeline._stages[1]._tracker  # 추적 단계가 받은 추적기

    assert isinstance(tracker, OpenCvObjectTracker)
    assert (tracker.algorithm, tracker.sample_ms, tracker.redetect_ms) == ("kcf", 50, 500)
    assert tracker.verify_boxes is False
    assert isinstance(tracker._locator._llm, ReplayLLMClient)


def test_opencv_tracker_without_a_vlm_key_says_how_to_turn_it_off(monkeypatch):
    pytest.importorskip("cv2")
    pytest.importorskip("openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ConfigurationError, match="FRAME_TRACKER=fake"):
        build_container(Settings(tracker="opencv"))


def test_an_unknown_algorithm_stops_the_server_from_starting():
    pytest.importorskip("cv2")
    with pytest.raises(ConfigurationError, match="FRAME_TRACKER_ALGORITHM"):
        build_container(Settings(tracker="opencv", tracker_algorithm="sam2", llm_provider="fake"))
