"""OpenCV 추적기가 프레임과 박스를 다루는 작은 함수들 (픽셀 <-> 비율 · 줄이기 · 잘라 내기 · JPEG · 겹침).

박스는 화면에 대한 비율(0..1, x·y 는 왼쪽 위)이다. 추적기 안에서는 (x, y, w, h) 튜플이나 Box 로 다룬다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from frame.adapters.shared.media.frames import Box

Ratio = tuple[float, float, float, float]  # (x, y, w, h) 비율 박스


def pixel_rect(image: Any, box: Box) -> tuple[int, int, int, int]:
    """비율 박스 -> 그 이미지의 픽셀 사각형 (가로·세로는 2px 이상)."""
    height, width = image.shape[:2]
    return (
        int(round(box.x * width)),
        int(round(box.y * height)),
        max(2, int(round(box.w * width))),
        max(2, int(round(box.h * height))),
    )


def normalized(rect: Sequence[float], width: int, height: int, min_side: float) -> Ratio | None:
    """픽셀 박스 -> 화면 안으로 자른 비율 박스. 가로나 세로가 min_side 밑으로 줄었으면 None (놓친 것)."""
    x, y, w, h = (float(v) for v in rect)
    left, top = max(0.0, x / width), max(0.0, y / height)
    right, bottom = min(1.0, (x + w) / width), min(1.0, (y + h) / height)
    if right - left < min_side or bottom - top < min_side:
        return None
    return round(left, 4), round(top, 4), round(right - left, 4), round(bottom - top, 4)


def rounded(box: Box) -> Ratio:
    return round(box.x, 4), round(box.y, 4), round(box.w, 4), round(box.h, 4)


def overlap(a: Ratio, b: Box) -> float:
    """두 박스의 겹침(IoU, 0..1)."""
    ax, ay, aw, ah = a
    left, top = max(ax, b.x), max(ay, b.y)
    right, bottom = min(ax + aw, b.x + b.w), min(ay + ah, b.y + b.h)
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    union = aw * ah + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def shrink(cv2: Any, image: Any, max_side: int) -> Any:
    """긴 변이 max_side 를 넘으면 줄인다 (비율 박스는 그대로 맞는다)."""
    height, width = image.shape[:2]
    scale = max_side / max(height, width)
    if scale >= 1:
        return image
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def crop(cv2: Any, image: Any, box: Box, *, padding: float, min_side: int) -> Any:
    """박스(여백 포함) 영역. 작으면 짧은 변을 min_side 까지 키운다 (작은 물건도 VLM 이 알아보게)."""
    height, width = image.shape[:2]
    region = box.padded(padding)
    left, top = int(region.x * width), int(region.y * height)
    right = max(left + 1, int(math.ceil((region.x + region.w) * width)))
    bottom = max(top + 1, int(math.ceil((region.y + region.h) * height)))
    piece = image[top:bottom, left:right]
    short = min(piece.shape[:2])
    if short < min_side:
        scale = min_side / short
        size = (int(round(piece.shape[1] * scale)), int(round(piece.shape[0] * scale)))
        piece = cv2.resize(piece, size, interpolation=cv2.INTER_CUBIC)
    return piece


def jpeg(cv2: Any, image: Any, quality: int) -> bytes | None:
    """JPEG 바이트. 바꾸지 못하면 None."""
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return bytes(encoded.tobytes()) if ok else None


def color_signature(cv2: Any, image: Any) -> Any:
    """프레임의 색 분포 (HSV 색상 30칸 x 채도 32칸 히스토그램, 0..1 로 맞춤). 연속 프레임끼리 견주어 컷을 찾는다."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
    cv2.normalize(histogram, histogram, 0, 1, cv2.NORM_MINMAX)
    return histogram


def signature_similarity(cv2: Any, a: Any, b: Any) -> float:
    """두 색 분포의 상관 (-1..1). 같은 장면의 연속 프레임은 1 에 가깝고, 컷이면 뚝 떨어진다."""
    return float(cv2.compareHist(a, b, cv2.HISTCMP_CORREL))
