"""두 영상의 구조 유사도(SSIM). 실패는 VideoToolError 다.

- ssim: 화면 전체. 보존 검수가 부모 클립과 새 클립을 견줄 때 쓴다.
- region_ssim: 화면의 한 영역 안에서만, 또는 그 영역을 뺀 나머지에서만 잰다. 국소 수정이 '고칠 곳만 바꾸고
  나머지는 그대로 뒀는지' 를 재는 데 쓴다 (ADR-0027).
ffmpeg 의 출력에서 SSIM 값을 읽고 확인하는 일은 둘이 같이 쓴다.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .ffmpeg import WORK_TIMEOUT_S, VideoToolError, run_reading, tail
from .probe import probe_media, video_size

__all__ = ["region_ssim", "ssim"]

_SSIM_ALL = re.compile(r"SSIM .*?All:([0-9.]+|nan|inf)")


def ssim(ffmpeg: str, first: bytes, second: bytes, timeout_s: float = WORK_TIMEOUT_S) -> float:
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
    return _ssim_value(code, log)


def region_ssim(
    ffmpeg: str,
    first: bytes,
    second: bytes,
    box: tuple[float, float, float, float],
    *,
    inside: bool,
    span_ms: tuple[int, int] | None = None,
    timeout_s: float = WORK_TIMEOUT_S,
) -> float:
    """두 영상의 SSIM 평균 (0..1) 을 화면의 일부에서만 잰다.

    box 는 정규화 좌표 (x, y, w, h). inside=True 면 그 영역만 잘라 견주고, False 면 그 영역을 검게 칠한 뒤
    나머지를 견준다. span_ms 를 주면 그 시간 구간의 프레임만 본다. 크기가 다르면 second 를 first 에 맞춘다.
    """
    x, y, w, h = box
    with tempfile.TemporaryDirectory(prefix="frame-ssim-") as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        a.write_bytes(first)
        b.write_bytes(second)
        size = probe_media(ffmpeg, a, timeout_s)
        if not size.has_video or not size.height:
            raise VideoToolError("견줄 영상에 영상 스트림이 없습니다")
        steps: list[str] = []
        if span_ms is not None:
            start, end = span_ms
            steps.append(f"trim=start={start / 1000:.3f}:end={end / 1000:.3f},setpts=PTS-STARTPTS")
        if inside:
            steps.append(f"crop=iw*{w:.6f}:ih*{h:.6f}:iw*{x:.6f}:ih*{y:.6f}")
        else:
            steps.append(
                f"drawbox=x=iw*{x:.6f}:y=ih*{y:.6f}:w=iw*{w:.6f}:h=ih*{h:.6f}:color=black:t=fill"
            )
        ops = ",".join(steps)
        graph = (
            f"[0:v]{ops}[a];[1:v]scale={size.width}:{size.height},setsar=1,{ops}[b];"
            "[a][b]ssim=shortest=1"
        )
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", str(a), "-i", str(b)]
        cmd += ["-filter_complex", graph, "-an", "-f", "null", "-"]
        code, log = run_reading(cmd, timeout_s)
    return _ssim_value(code, log)


def _ssim_value(code: int, log: str) -> float:
    """ffmpeg 의 ssim 필터가 끝에 찍는 평균값 (0..1). 실패했거나 값이 수가 아니면 VideoToolError."""
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
