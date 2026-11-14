"""장면 클립에서 프레임 뽑기 (정체성 기준 추출기와 검수기가 함께 쓴다).

extract_frame 은 한 시각의 프레임을 이미지(JPEG/PNG)로 돌려준다. 정규화 좌표(0..1)의 사각형(Box)으로 잘라 내거나,
긴 변을 줄이거나, 짧은 변을 키울 수 있다. numpy·OpenCV 같은 추가 패키지 없이 ffmpeg 하나로 처리한다.
ffmpeg 실행 파일 경로는 조립 단계(config/registrations)가 찾아서 넘긴다. 실패는 VideoToolError 다.

어댑터는 extract_frame 을 바로 부르지 않고 프레임 표본기(FrameSampler)를 거친다: 검수기 셋과 정체성 추출기가
같은 클립의 같은 시각을 보므로, 한 번 뽑은 프레임을 다시 쓴다. 표본 시각은 midpoints 가 한 곳에서 계산한다.
"""

from __future__ import annotations

import hashlib
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from .ffmpeg import VideoToolError, run_reading, tail

__all__ = ["Box", "ClipFrames", "FrameSampler", "Image", "extract_frame", "midpoints"]

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


def midpoints(duration_ms: int, count: int) -> list[int]:
    """길이를 count 등분한 구간마다의 가운데 시각(ms). 어댑터들이 같은 시각을 보도록 한 곳에서 계산한다."""
    return [int(duration_ms * (index + 0.5) / count) for index in range(count)]


class FrameSampler:
    """클립에서 프레임을 뽑되, 같은 클립의 같은 프레임은 다시 뽑지 않는다.

    프레임 하나를 뽑을 때마다 클립 전체를 임시 파일에 쓰고 ffmpeg 를 띄운다. 요구 반영 검수와 일관성 검수는 같은 장면의
    같은 시각을 보고, 정체성 추출기도 같은 클립을 읽는다. 그래서 최근에 뽑은 capacity 장을 들고 있다가 다시 쓴다.
    - 클립은 내용(바이트의 해시)으로 가린다. 같은 주소에 다른 영상이 다시 저장돼도 예전 프레임을 돌려주지 않는다.
    - 실패한 추출은 기억하지 않는다.
    - 여러 스레드가 함께 써도 된다 (추출은 잠금 밖에서 한다. 같은 프레임을 두 스레드가 동시에 뽑을 수는 있다).
    """

    _shared: ClassVar[dict[str, FrameSampler]] = {}
    _shared_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        ffmpeg: str,
        *,
        capacity: int = 128,
        extract: Callable[..., Image] | None = None,
    ) -> None:
        self.ffmpeg = ffmpeg
        self._capacity = max(1, capacity)
        self._extract: Callable[..., Image] = extract or extract_frame
        self._frames: OrderedDict[tuple[object, ...], Image] = OrderedDict()
        self._lock = threading.Lock()

    @classmethod
    def shared(cls, ffmpeg: str) -> FrameSampler:
        """그 ffmpeg 로 뽑는 표본기 (프로세스에 하나). 어댑터들이 같은 표본기를 써야 서로 뽑은 프레임을 다시 쓴다."""
        with cls._shared_lock:
            sampler = cls._shared.get(ffmpeg)
            if sampler is None:
                sampler = cls._shared[ffmpeg] = cls(ffmpeg)
            return sampler

    def clip(self, video: bytes) -> ClipFrames:
        """이 클립에서 프레임을 뽑는 손잡이. 클립의 해시는 여기서 한 번만 계산한다."""
        return ClipFrames(self, video)

    def _frame(
        self, digest: str, video: bytes, at_ms: int, timeout_s: float, **shape: object
    ) -> Image:
        key = (digest, at_ms, *sorted(shape.items()))
        with self._lock:
            found = self._frames.get(key)
            if found is not None:
                self._frames.move_to_end(key)
                return found
        image = self._extract(self.ffmpeg, video, at_ms, timeout_s=timeout_s, **shape)
        with self._lock:
            self._frames[key] = image
            self._frames.move_to_end(key)
            while len(self._frames) > self._capacity:
                self._frames.popitem(last=False)
        return image


class ClipFrames:
    """한 클립의 프레임. FrameSampler.clip(video) 로 만든다."""

    def __init__(self, sampler: FrameSampler, video: bytes) -> None:
        self._sampler = sampler
        self._video = video
        self._digest = hashlib.sha256(video).hexdigest()

    def at(
        self,
        at_ms: int,
        *,
        crop: Box | None = None,
        max_side: int | None = None,
        min_side: int | None = None,
        image_format: str = "jpg",
        timeout_s: float = 120.0,
    ) -> Image:
        """at_ms 시각의 프레임. 인자의 뜻은 extract_frame 과 같다.

        시간 제한은 결과를 바꾸지 않으므로 프레임을 가리는 데 쓰지 않는다.
        """
        return self._sampler._frame(
            self._digest,
            self._video,
            at_ms,
            timeout_s,
            crop=crop,
            max_side=max_side,
            min_side=min_side,
            image_format=image_format,
        )
