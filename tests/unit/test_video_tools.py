"""ffmpeg 공용 도구 (프레임 추출·크기·SSIM). 실제 ffmpeg 로 합성 클립을 다룬다 (없으면 건너뜀)."""

from __future__ import annotations

import tempfile

import pytest

from frame.adapters.video_tools import (
    Box,
    VideoToolError,
    extract_frame,
    require_ffmpeg,
    ssim,
    video_size,
)
from frame.application.errors import ConfigurationError
from tests.support.video import find_ffmpeg, make_clip

FFMPEG = find_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    folder = tmp_path_factory.mktemp("clips")
    return {
        "pattern": make_clip(FFMPEG, folder / "pattern.mp4"),
        "red": make_clip(FFMPEG, folder / "red.mp4", source="color=c=red"),
        "blue": make_clip(FFMPEG, folder / "blue.mp4", source="color=c=blue"),
        "large": make_clip(FFMPEG, folder / "large.mp4", size="128x228"),
        "large_red": make_clip(
            FFMPEG, folder / "large_red.mp4", size="128x228", source="color=c=red"
        ),
    }


def test_extract_frame_returns_an_image_of_the_clip_size(clips):
    frame = extract_frame(FFMPEG, clips["pattern"], 2000)

    assert frame.media_type == "image/jpeg" and frame.data[:2] == b"\xff\xd8"
    assert video_size(FFMPEG, frame.data) == (64, 114)
    png = extract_frame(FFMPEG, clips["pattern"], 0, image_format="png")
    assert png.media_type == "image/png" and png.data[:4] == b"\x89PNG"


def test_frames_at_different_times_differ(clips):
    assert extract_frame(FFMPEG, clips["pattern"], 500).data != (
        extract_frame(FFMPEG, clips["pattern"], 3000).data
    )


def test_crop_uses_normalized_coordinates(clips):
    half = extract_frame(FFMPEG, clips["pattern"], 1000, crop=Box(0.5, 0.0, 0.5, 1.0))
    assert video_size(FFMPEG, half.data) == (32, 114)


def test_max_side_only_shrinks(clips):
    small = extract_frame(FFMPEG, clips["large"], 1000, max_side=114)
    assert video_size(FFMPEG, small.data) == (64, 114)
    untouched = extract_frame(FFMPEG, clips["pattern"], 1000, max_side=512)
    assert video_size(FFMPEG, untouched.data) == (64, 114)


def test_min_side_only_enlarges(clips):
    grown = extract_frame(FFMPEG, clips["pattern"], 1000, min_side=128)
    assert video_size(FFMPEG, grown.data) == (128, 228)
    untouched = extract_frame(FFMPEG, clips["large"], 1000, min_side=64)
    assert video_size(FFMPEG, untouched.data) == (128, 228)
    cropped = extract_frame(
        FFMPEG, clips["pattern"], 1000, crop=Box(0.0, 0.0, 0.5, 0.5), min_side=96
    )
    assert min(video_size(FFMPEG, cropped.data)) == 96


def test_box_padding_grows_and_stays_inside_the_frame():
    box = Box(0.4, 0.4, 0.2, 0.2).padded(0.5)
    assert (round(box.x, 3), round(box.y, 3), round(box.w, 3), round(box.h, 3)) == (
        0.3,
        0.3,
        0.4,
        0.4,
    )
    edge = Box(0.0, 0.8, 0.5, 0.2).padded(0.5)
    assert edge.x == 0.0 and round(edge.y + edge.h, 6) == 1.0
    assert edge.as_dict() == {"x": edge.x, "y": edge.y, "w": edge.w, "h": edge.h}


def test_ssim_is_one_for_the_same_clip_and_lower_for_different_ones(clips):
    assert ssim(FFMPEG, clips["red"], clips["red"]) == pytest.approx(1.0, abs=1e-3)
    assert ssim(FFMPEG, clips["red"], clips["blue"]) < 0.9
    assert ssim(FFMPEG, clips["pattern"], clips["red"]) < 0.5


def test_ssim_compares_clips_of_different_sizes(clips):
    # 같은 내용, 크기만 다르다: 둘째를 첫째 크기에 맞춰 비교한다
    assert ssim(FFMPEG, clips["red"], clips["large_red"]) == pytest.approx(1.0, abs=1e-2)
    assert ssim(FFMPEG, clips["blue"], clips["large_red"]) < 0.9


def test_ssim_works_on_very_short_clips(tmp_path):
    """프레임이 몇 장뿐인 클립도 비교한다 (scale2ref 는 여기서 프레임을 내지 못했다)."""
    red = make_clip(FFMPEG, tmp_path / "r.mp4", seconds=0.5, source="color=c=red")
    blue = make_clip(FFMPEG, tmp_path / "b.mp4", seconds=0.5, source="color=c=blue")
    assert ssim(FFMPEG, red, red) == pytest.approx(1.0, abs=1e-3)
    assert ssim(FFMPEG, red, blue) < 0.9


def test_garbage_input_raises_a_video_tool_error():
    with pytest.raises(VideoToolError):
        extract_frame(FFMPEG, b"not a video", 0)
    with pytest.raises(VideoToolError):
        ssim(FFMPEG, b"not a video", b"neither")
    with pytest.raises(VideoToolError):
        video_size(FFMPEG, b"not a video")


def test_error_messages_do_not_show_the_servers_temporary_folder():
    """ffmpeg 는 읽지 못한 파일의 경로를 출력한다. 그 꼬리가 검수 결과의 사유로 API 에 나간다 (감사 A-29)."""
    with pytest.raises(VideoToolError) as raised:
        extract_frame(FFMPEG, b"not a video", 0)

    message = str(raised.value)
    assert tempfile.gettempdir() not in message and "frame-frames-" not in message


def test_missing_ffmpeg_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="ffmpeg"):
        require_ffmpeg(None)
    assert require_ffmpeg(FFMPEG) == FFMPEG
