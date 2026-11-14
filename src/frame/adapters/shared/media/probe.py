"""영상 정보 읽기: 영상·이미지의 크기.

ffprobe 없이 ffmpeg 하나로 읽는다 (imageio-ffmpeg 는 ffprobe 를 담고 있지 않다). 실패는 VideoToolError 다.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .ffmpeg import VideoToolError, run_reading, tail

__all__ = ["video_size"]

_SIZE = re.compile(r"Stream #\d+:\d+.*?: Video: .*?, (\d{2,5})x(\d{2,5})")


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
