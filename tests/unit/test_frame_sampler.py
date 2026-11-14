"""프레임 표본기 (FrameSampler): 같은 클립의 같은 프레임을 다시 뽑지 않는다 (감사 R-10).

검수기 셋과 정체성 추출기가 같은 클립의 같은 시각을 본다. 프레임 하나를 뽑을 때마다 클립 전체를 임시 파일에 쓰고
ffmpeg 를 띄우므로, 한 번 뽑은 프레임은 다시 쓴다. 여기서는 추출 함수를 가짜로 바꿔 몇 번 불렸는지를 본다.
"""

from __future__ import annotations

import pytest

from frame.adapters.shared.media.ffmpeg import VideoToolError
from frame.adapters.shared.media.frames import Box, FrameSampler, Image, midpoints


class _Extractor:
    """extract_frame 자리에 꽂는 가짜: 부른 인자를 적고, 인자마다 다른 이미지를 돌려준다."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail = False

    def __call__(self, ffmpeg, video, at_ms, **options) -> Image:
        self.calls.append((ffmpeg, bytes(video), at_ms, tuple(sorted(options.items()))))
        if self.fail:
            raise VideoToolError("프레임을 뽑지 못했습니다")
        return Image(f"{len(self.calls)}".encode(), "image/jpeg")


@pytest.fixture
def extractor() -> _Extractor:
    return _Extractor()


def test_midpoints_are_the_middle_of_each_equal_part():
    assert midpoints(4000, 1) == [2000]
    assert midpoints(4000, 3) == [666, 2000, 3333]
    assert midpoints(0, 2) == [0, 0]
    assert midpoints(4000, 0) == []


def test_the_same_frame_of_the_same_clip_is_extracted_once(extractor):
    sampler = FrameSampler("ffmpeg", extract=extractor)

    first = sampler.clip(b"video").at(2000, max_side=1024)
    again = sampler.clip(b"video").at(2000, max_side=1024)  # 다른 검수기가 같은 클립을 다시 읽었다

    assert again is first
    assert len(extractor.calls) == 1
    assert extractor.calls[0][:3] == ("ffmpeg", b"video", 2000)


def test_a_different_time_option_or_clip_is_a_different_frame(extractor):
    sampler = FrameSampler("ffmpeg", extract=extractor)
    clip = sampler.clip(b"video")

    frames = [
        clip.at(2000, max_side=1024),
        clip.at(3000, max_side=1024),
        clip.at(2000, max_side=512),
        clip.at(2000, crop=Box(0.1, 0.1, 0.5, 0.5)),
        clip.at(2000, crop=Box(0.1, 0.1, 0.5, 0.6)),
        clip.at(2000, max_side=1024, image_format="png"),
        sampler.clip(b"other video").at(2000, max_side=1024),
    ]

    assert len(extractor.calls) == len(frames) == len({frame.data for frame in frames})


def test_a_failed_extraction_is_not_remembered(extractor):
    sampler = FrameSampler("ffmpeg", extract=extractor)
    extractor.fail = True
    with pytest.raises(VideoToolError):
        sampler.clip(b"video").at(2000)

    extractor.fail = False
    assert sampler.clip(b"video").at(2000).data == b"2"  # 다시 뽑는다


def test_only_the_most_recent_frames_are_kept(extractor):
    sampler = FrameSampler("ffmpeg", capacity=2, extract=extractor)
    clip = sampler.clip(b"video")
    clip.at(1000)
    clip.at(2000)
    clip.at(1000)  # 다시 썼으니 최근 것이 된다
    clip.at(3000)  # 가장 오래 안 쓴 2000 이 밀려난다

    assert len(extractor.calls) == 3
    clip.at(1000)
    assert len(extractor.calls) == 3
    clip.at(2000)
    assert len(extractor.calls) == 4


def test_one_sampler_is_shared_per_ffmpeg_executable():
    assert FrameSampler.shared("ffmpeg-a") is FrameSampler.shared("ffmpeg-a")
    assert FrameSampler.shared("ffmpeg-a") is not FrameSampler.shared("ffmpeg-b")
