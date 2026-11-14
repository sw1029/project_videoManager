"""실제 ffmpeg 로 만든 작은 시험용 영상·소리 (컷이 있는 영상, 순음). ffmpeg 가 없으면 테스트가 건너뛴다."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def make_cut_clip(
    ffmpeg: str,
    path: Path,
    *,
    colors: Sequence[str] = ("red", "blue", "green"),
    seconds_each: float = 2.0,
    size: str = "64x114",
    fps: int = 10,
    tone_hz: int | None = None,
) -> bytes:
    """단색 구간이 이어진 영상: 색이 바뀌는 곳이 컷이다. tone_hz 를 주면 그 높이의 소리가 든다."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for color in colors:
        cmd += [
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:size={size}:rate={fps}:duration={seconds_each}",
        ]
    total = seconds_each * len(colors)
    if tone_hz is not None:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=16000:duration={total}"]
    inputs = "".join(f"[{i}:v]" for i in range(len(colors)))
    cmd += ["-filter_complex", f"{inputs}concat=n={len(colors)}:v=1:a=0[v]", "-map", "[v]"]
    if tone_hz is not None:
        cmd += ["-map", f"{len(colors)}:a", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    _run(cmd)
    return path.read_bytes()


def make_tone(
    ffmpeg: str, path: Path, *, seconds: float = 3.0, hz: int = 440, sample_rate: int = 16000
) -> bytes:
    """순음 소리 파일 (확장자에 따라 wav·mp3·m4a)."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i"]
    cmd += [
        f"sine=frequency={hz}:sample_rate={sample_rate}:duration={seconds}",
        "-ac",
        "1",
        str(path),
    ]
    _run(cmd)
    return path.read_bytes()
