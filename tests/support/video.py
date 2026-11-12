"""실제 ffmpeg 로 만든 작은 합성 클립 (프레임 추출·SSIM 테스트용). ffmpeg 가 없으면 테스트가 건너뛴다."""

from __future__ import annotations

import subprocess
from pathlib import Path

from frame.adapters.generator.ffmpeg import resolve_ffmpeg
from frame.application.errors import ConfigurationError


def find_ffmpeg() -> str | None:
    try:
        return resolve_ffmpeg()
    except ConfigurationError:
        return None


def make_clip(
    ffmpeg: str,
    path: Path,
    *,
    seconds: float = 4.0,
    size: str = "64x114",
    fps: int = 10,
    source: str = "testsrc2",
) -> bytes:
    """움직이는 시험 무늬(testsrc2) 또는 단색(color=c=red 등) 클립. 9:16 에 가까운 작은 크기."""
    pattern = source if "=" in source else f"{source}="
    separator = "" if pattern.endswith("=") else ":"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i"]
    cmd += [f"{pattern}{separator}size={size}:rate={fps}:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path.read_bytes()
