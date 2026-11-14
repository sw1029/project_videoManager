"""ffmpeg 로 장면 클립을 읽는 공용 도구 (정체성 기준 추출기·검수기가 함께 쓴다).

- extract_frame: 한 시각의 프레임을 이미지(JPEG/PNG)로. 정규화 좌표(0..1)로 잘라 내거나 긴 변을 줄일 수 있다.
- video_size: 영상·이미지의 크기.
- ssim: 두 영상의 구조 유사도 평균(0..1). 크기가 다르면 첫 영상 크기에 맞춘다.
ffmpeg 실행 파일 경로는 조립 단계(레지스트리)가 찾아서 넘긴다. 실패는 VideoToolError 로 알린다.
numpy·OpenCV 같은 추가 패키지 없이 ffmpeg 하나로 처리한다.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from frame.adapters.shared.media.ffmpeg import FfmpegNotRun, run_ffmpeg, tail
from frame.application.errors import AdapterError, ConfigurationError

_SIZE = re.compile(r"Stream #\d+:\d+.*?: Video: .*?, (\d{2,5})x(\d{2,5})")
_SSIM_ALL = re.compile(r"SSIM .*?All:([0-9.]+|nan|inf)")
_FORMATS = {
    "jpg": ("image/jpeg", ["-c:v", "mjpeg", "-q:v", "3", "-f", "image2"]),
    "png": ("image/png", ["-c:v", "png", "-f", "image2"]),
}


class VideoToolError(AdapterError):
    """ffmpeg 로 영상을 읽거나 비교하지 못했다."""

    code = "VIDEO_TOOL_ERROR"


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


def require_ffmpeg(ffmpeg: str | None) -> str:
    if not ffmpeg:
        raise ConfigurationError(
            "ffmpeg 실행 파일이 필요합니다. video extra(imageio-ffmpeg)를 설치하거나 "
            "FRAME_VIDEO_FFMPEG 로 경로를 지정하세요"
        )
    return ffmpeg


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
        code, log = _run(cmd, timeout_s)
        if code != 0 or not target.is_file() or target.stat().st_size == 0:
            raise VideoToolError(f"프레임을 뽑지 못했습니다 ({at_ms} ms): {tail(log)}")
        return Image(target.read_bytes(), media_type)


def video_size(ffmpeg: str, media: bytes, timeout_s: float = 60.0) -> tuple[int, int]:
    """영상 또는 이미지의 (가로, 세로)."""
    with tempfile.TemporaryDirectory(prefix="frame-frames-") as tmp:
        source = Path(tmp) / "media.bin"
        source.write_bytes(media)
        _, log = _run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(source)], timeout_s)
    match = _SIZE.search(log)
    if match is None:
        raise VideoToolError(f"영상 크기를 읽지 못했습니다: {tail(log)}")
    return int(match.group(1)), int(match.group(2))


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
        code, log = _run(cmd, timeout_s)
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


def _run(cmd: list[str], timeout_s: float) -> tuple[int, str]:
    """공용 실행기로 돌린다. 실행 자체가 안 되면 이 모듈의 오류(VideoToolError)로 올린다."""
    try:
        return run_ffmpeg(cmd, timeout_s)
    except FfmpegNotRun as exc:
        raise VideoToolError(str(exc)) from exc
