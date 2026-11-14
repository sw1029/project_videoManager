"""원본 영상 분석 포트의 계약 테스트 (ADR-0025): MediaProber · ShotDetector.

Fake 와 ffmpeg 구현이 같은 규약을 지킨다. ffmpeg 구현은 진짜 ffmpeg 로 합성한 영상을 읽는다 (없으면 건너뜀).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from frame.adapters.series.source_analysis import FakeMediaProber, FakeShotDetector
from frame.adapters.series.source_analysis.ffmpeg import FfmpegMediaProber, FfmpegShotDetector
from frame.adapters.shared.media.fake_media import fake_video
from frame.application.ports.series.source_analysis import (
    MediaProber,
    ShotDetector,
    VideoAnalysisError,
)
from frame.config.theme_settings import ThemeSettings
from frame.domain.series.shortform import check_platforms
from frame.domain.series.source_analysis import (
    validate_shots,
)
from tests.support.media import make_cut_clip
from tests.support.speech import SAID
from tests.support.video import find_ffmpeg, make_clip

FFMPEG = find_ffmpeg()


# ---- MediaProber · ShotDetector ----
@dataclass
class Kit:
    name: str
    prober: MediaProber
    detector: ShotDetector
    video: bytes  # 64x114, 10 fps, 2초씩 세 구간 (컷 2000·4000 ms)
    sounding: bytes  # 같은 영상에 소리가 든 것
    uncut: bytes  # 컷이 없는 4초 영상


def _fake_kit() -> Kit:
    shape = {"width": 64, "height": 114, "fps": 10.0}
    return Kit(
        "fake",
        FakeMediaProber(),
        FakeShotDetector(),
        fake_video(duration_ms=6000, cuts_ms=(2000, 4000), **shape),
        fake_video(duration_ms=6000, cuts_ms=(2000, 4000), transcript=SAID, **shape),
        fake_video(duration_ms=4000, cuts_ms=(), **shape),
    )


def _ffmpeg_kit(folder) -> Kit:
    return Kit(
        "ffmpeg",
        FfmpegMediaProber(FFMPEG),
        FfmpegShotDetector(FFMPEG, threshold=ThemeSettings().shot_threshold),
        make_cut_clip(FFMPEG, folder / "cuts.mp4"),
        make_cut_clip(FFMPEG, folder / "sounding.mp4", tone_hz=440),
        make_clip(FFMPEG, folder / "uncut.mp4", source="color=c=red"),
    )


@pytest.fixture(scope="module", params=["fake", "ffmpeg"])
def kit(request, tmp_path_factory) -> Kit:
    if request.param == "fake":
        return _fake_kit()
    if FFMPEG is None:
        pytest.skip("ffmpeg 없음 (video extra 미설치)")
    return _ffmpeg_kit(tmp_path_factory.mktemp("analysis"))


def test_probe_reports_shape_length_and_size(kit):
    info = kit.prober.probe(kit.video)

    assert (info.width, info.height) == (64, 114)
    assert info.duration_ms == 6000 and info.fps == 10.0
    assert info.has_audio is False and info.size_bytes == len(kit.video)
    assert kit.prober.probe(kit.sounding).has_audio is True
    assert info.orientation == "vertical"
    assert check_platforms(info).within  # 겉모습은 그대로 숏폼 규격 판정에 들어간다


def test_probe_rejects_what_is_not_a_video(kit):
    with pytest.raises(VideoAnalysisError):
        kit.prober.probe(b"not a video at all")


def test_shots_are_contiguous_and_end_at_the_video_end(kit):
    media = kit.prober.probe(kit.video)
    shots = kit.detector.detect(kit.video, media)

    validate_shots(shots, media.duration_ms)
    assert [(s.start_ms, s.end_ms) for s in shots] == [(0, 2000), (2000, 4000), (4000, 6000)]


def test_video_without_cuts_is_one_shot(kit):
    media = kit.prober.probe(kit.uncut)
    shots = kit.detector.detect(kit.uncut, media)

    assert [(s.start_ms, s.end_ms) for s in shots] == [(0, 4000)]


def test_shot_detector_rejects_what_is_not_a_video(kit):
    media = kit.prober.probe(kit.video)
    with pytest.raises(VideoAnalysisError):
        kit.detector.detect(b"not a video at all", media)


def test_motion_jpeg_is_not_in_the_list_of_accepted_codecs():
    """받는 코덱 목록과 동작이 같아야 한다 (감사 BTS-D36).

    MJPEG 는 영상 정보를 읽는 쪽이 '그림 한 장'(음악 파일의 표지)으로 본다. 목록에 있어도 거기까지 가지 못했다.
    """
    from frame.adapters.series.source_analysis.ffmpeg import VIDEO_CODECS
    from frame.adapters.shared.media.media_tools import _STILL_CODECS

    assert "mjpeg" not in VIDEO_CODECS and not (VIDEO_CODECS & _STILL_CODECS)


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
def test_text_is_not_read_as_a_video():
    """줄바꿈 없는 글 800바이트가 640x80 영상으로 읽혔다 (임시 파일의 확장자 때문에 ffmpeg 가 글 형식을 골랐다)."""
    from frame.adapters.series.source_analysis.ffmpeg import FfmpegMediaProber

    with pytest.raises(VideoAnalysisError):
        FfmpegMediaProber(FFMPEG).probe(b"frame " * 134)
