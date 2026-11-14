"""두 영상의 구조 유사도(SSIM): 보존 검수가 부모 클립과 새 클립을 견줄 때 쓴다. 실패는 VideoToolError 다."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .ffmpeg import VideoToolError, run_reading, tail
from .probe import video_size

__all__ = ["ssim"]

_SSIM_ALL = re.compile(r"SSIM .*?All:([0-9.]+|nan|inf)")


def ssim(ffmpeg: str, first: bytes, second: bytes, timeout_s: float = 300.0) -> float:
    """두 영상의 프레임별 SSIM 평균 (0..1, 1 이면 같다). 크기가 다르면 second 를 first 크기에 맞춘다.

    프레임 수가 다르면 짧은 쪽까지만 비교한다.
    """
    width, height = video_size(ffmpeg, first, timeout_s)
    with tempfile.TemporaryDirectory(prefix="frame-ssim-") as tmp:
        a, b = Path(tmp) / "a.mp4", Path(tmp) / "b.mp4"
        a.write_bytes(first)
        b.write_bytes(second)
        # scale2ref 는 짧은 클립에서 프레임을 내지 못하는 경우가 있어, 크기를 먼저 읽고 scale 로 맞춘다
        graph = f"[1:v]scale={width}:{height},setsar=1[other];[0:v][other]ssim=shortest=1"
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", str(a), "-i", str(b)]
        cmd += ["-filter_complex", graph, "-an", "-f", "null", "-"]
        code, log = run_reading(cmd, timeout_s)
    match = _SSIM_ALL.search(log)
    if code != 0 or match is None:
        raise VideoToolError(f"두 영상을 비교하지 못했습니다: {tail(log)}")
    try:
        value = float(match.group(1))
    except ValueError as exc:
        raise VideoToolError(f"SSIM 값을 읽지 못했습니다: {match.group(1)}") from exc
    if value != value or value in (float("inf"), float("-inf")):  # nan·inf
        raise VideoToolError(f"SSIM 값이 수가 아닙니다: {match.group(1)}")
    return max(0.0, min(1.0, value))
