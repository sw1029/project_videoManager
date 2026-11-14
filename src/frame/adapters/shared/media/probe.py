"""영상 정보 읽기: 영상·이미지의 크기(video_size)와 영상 스트림의 정보(probe_video).

ffprobe 없이 ffmpeg 하나로 읽는다 (imageio-ffmpeg 는 ffprobe 를 담고 있지 않다). 실패는 VideoToolError 다.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .ffmpeg import VideoToolError, run_reading, tail

__all__ = ["VideoProbe", "probe_video", "video_size"]

_SIZE = re.compile(r"Stream #\d+:\d+.*?: Video: .*?, (\d{2,5})x(\d{2,5})")
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_LINE = re.compile(r"Stream #\d+:\d+.*?: Video: .*")
_CODEC = re.compile(r"Video: (\w+)")
_LINE_SIZE = re.compile(r", (\d{2,5})x(\d{2,5})")
_FPS = re.compile(r"(\d+(?:\.\d+)?) (?:fps|tbr)")
_AUDIO_LINE = re.compile(r"Stream #\d+:\d+.*?: Audio: ")
_FRAMES = re.compile(r"frame=\s*(\d+)")


@dataclass(frozen=True)
class VideoProbe:
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    frames: int | None = None
    duration_ms: int | None = None
    has_audio: bool = False
    codec: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def video_size(ffmpeg: str, media: bytes, timeout_s: float = 60.0) -> tuple[int, int]:
    """영상 또는 이미지의 (가로, 세로)."""
    with tempfile.TemporaryDirectory(prefix="frame-frames-") as tmp:
        source = Path(tmp) / "media.bin"
        source.write_bytes(media)
        _, log = run_reading([ffmpeg, "-hide_banner", "-nostdin", "-i", str(source)], timeout_s)
    match = _SIZE.search(log)
    if match is None:
        raise VideoToolError(f"영상 크기를 읽지 못했습니다: {tail(log)}")
    return int(match.group(1)), int(match.group(2))


def probe_video(ffmpeg: str, path: str | Path, timeout_s: float = 120.0) -> VideoProbe:
    """첫 영상 스트림의 코덱·크기·fps·프레임 수·길이와 오디오 유무 (스트림 복사로 끝까지 읽어 프레임을 센다).

    읽지 못하면 VideoToolError 다. 생성기의 정규화와 조립기는 그것을 생성 오류로 바꿔 올린다.
    """
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)]
    cmd += ["-map", "0:v:0", "-c", "copy", "-f", "null", "-"]
    code, log = run_reading(cmd, timeout_s)
    if code != 0:
        raise VideoToolError(f"ffmpeg 가 영상을 읽지 못했습니다: {tail(log)}")
    video_line = _VIDEO_LINE.search(log)
    if video_line is None:
        raise VideoToolError(f"ffmpeg 가 영상 스트림을 찾지 못했습니다: {tail(log)}")
    line = video_line.group(0)
    codec = _CODEC.search(line)
    size = _LINE_SIZE.search(line)
    fps = _FPS.search(line)
    frames = _FRAMES.findall(log)
    return VideoProbe(
        width=int(size.group(1)) if size else None,
        height=int(size.group(2)) if size else None,
        fps=float(fps.group(1)) if fps else None,
        frames=int(frames[-1]) if frames else None,
        duration_ms=_duration_ms(log),
        has_audio=_AUDIO_LINE.search(log) is not None,
        codec=codec.group(1) if codec else None,
    )


def _duration_ms(log: str) -> int | None:
    match = _DURATION.search(log)
    if match is None:
        return None
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return round((hours * 3600 + minutes * 60 + seconds) * 1000)
