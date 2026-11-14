"""장면 클립에서 프레임 뽑기 (정체성 기준 추출기와 검수기가 함께 쓴다).

extract_frame 은 한 시각의 프레임을 이미지(JPEG/PNG)로 돌려준다. 정규화 좌표(0..1)의 사각형(Box)으로 잘라 내거나,
긴 변을 줄이거나, 짧은 변을 키울 수 있다. numpy·OpenCV 같은 추가 패키지 없이 ffmpeg 하나로 처리한다.
ffmpeg 실행 파일 경로는 조립 단계(config/registrations)가 찾아서 넘긴다. 실패는 VideoToolError 다.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import VideoToolError, run_reading, tail

__all__ = ["Box", "Image", "extract_frame"]

_FORMATS = {
    "jpg": ("image/jpeg", ["-c:v", "mjpeg", "-q:v", "3", "-f", "image2"]),
    "png": ("image/png", ["-c:v", "png", "-f", "image2"]),
}


@dataclass(frozen=True)
class Box:
    """정규화 좌표(0..1)의 사각형. x·y 는 왼쪽 위."""

    x: float
    y: float
    w: float
    h: float

    def padded(self, ratio: float) -> Box:
        """가로·세로를 ratio 만큼 넓히고 화면 안으로 자른다."""
        dx, dy = self.w * ratio, self.h * ratio
        left, top = max(0.0, self.x - dx), max(0.0, self.y - dy)
        right, bottom = min(1.0, self.x + self.w + dx), min(1.0, self.y + self.h + dy)
        return Box(left, top, right - left, bottom - top)

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass(frozen=True)
class Image:
    data: bytes
    media_type: str


def extract_frame(
    ffmpeg: str,
    video: bytes,
    at_ms: int,
    *,
    crop: Box | None = None,
    max_side: int | None = None,
    min_side: int | None = None,
    image_format: str = "jpg",
    timeout_s: float = 120.0,
) -> Image:
    """video 의 at_ms 시각 프레임. crop 은 정규화 좌표, max_side 는 긴 변의 상한(픽셀).

    min_side 를 주면 짧은 변이 그보다 작은 이미지를 비율을 지켜 키운다 (참조 이미지 최소 크기를 맞출 때).
    """
    media_type, codec = _FORMATS[image_format]
    filters: list[str] = []
    if crop is not None:
        filters.append(f"crop=iw*{crop.w:.6f}:ih*{crop.h:.6f}:iw*{crop.x:.6f}:ih*{crop.y:.6f}")
    if max_side is not None:
        # 긴 변이 max_side 를 넘을 때만 줄인다 (늘리지 않는다). 짝수 크기로 맞춘다
        filters.append(
            f"scale='if(gt(max(iw,ih),{max_side}),if(gt(iw,ih),{max_side},-2),iw)'"
            f":'if(gt(max(iw,ih),{max_side}),if(gt(iw,ih),-2,{max_side}),ih)'"
        )
    if min_side is not None:
        # 짧은 변이 min_side 보다 작을 때만 키운다
        filters.append(
            f"scale='if(lt(min(iw,ih),{min_side}),if(lt(iw,ih),{min_side},-2),iw)'"
            f":'if(lt(min(iw,ih),{min_side}),if(lt(iw,ih),-2,{min_side}),ih)':flags=lanczos"
        )
    with tempfile.TemporaryDirectory(prefix="frame-frames-") as tmp:
        source = Path(tmp) / "clip.mp4"
        target = Path(tmp) / f"frame.{image_format}"
        source.write_bytes(video)
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y"]
        cmd += ["-ss", f"{max(0, at_ms) / 1000:.3f}", "-i", str(source), "-frames:v", "1"]
        if filters:
            cmd += ["-vf", ",".join(filters)]
        cmd += [*codec, str(target)]
        code, log = run_reading(cmd, timeout_s)
        if code != 0 or not target.is_file() or target.stat().st_size == 0:
            raise VideoToolError(f"프레임을 뽑지 못했습니다 ({at_ms} ms): {tail(log)}")
        return Image(target.read_bytes(), media_type)
