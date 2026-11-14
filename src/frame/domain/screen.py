"""화면 위의 영역과 문구의 종류 (정규화 좌표).

영상 분석(화면 문구 읽기), 테마의 문구 규칙, 후반 합성(문구 덧씌우기), 국소 수정이 함께 쓴다.
좌표는 화면의 가로·세로를 1 로 본 값이고 x·y 는 왼쪽 위다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import ValidationError

_EDGE_TOLERANCE = 1e-6


class TextRole(StrEnum):
    """화면 문구가 하는 일."""

    TITLE = "TITLE"  # 제목·머리글
    CAPTION = "CAPTION"  # 말하는 내용을 받아 적은 자막
    LABEL = "LABEL"  # 이름표·설명 문구
    PRICE = "PRICE"  # 가격·수치
    CALL_TO_ACTION = "CALL_TO_ACTION"  # 구독·방문 유도
    WATERMARK = "WATERMARK"  # 로고·계정명
    OTHER = "OTHER"


class TextSource(StrEnum):
    """문구가 어디에 있는가. 고치는 방법이 여기서 갈린다."""

    OVERLAY = "OVERLAY"  # 편집에서 얹은 문구: 후반 합성 계획의 항목이라 그 항목만 고치면 된다
    IN_SCENE = "IN_SCENE"  # 장면 안에 찍힌 글자(간판·제품 라벨): 생성 모델이 그린 픽셀이다


@dataclass(frozen=True)
class Region:
    """정규화 좌표(0..1)의 사각형."""

    x: float
    y: float
    w: float
    h: float

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.w, self.h)
        if any(not isinstance(v, int | float) or not math.isfinite(v) for v in values):
            raise ValidationError(f"영역 좌표가 수가 아닙니다: {values}")
        if self.w <= 0 or self.h <= 0:
            raise ValidationError(f"영역의 크기가 0 이하입니다: {values}")
        if (
            self.x < -_EDGE_TOLERANCE
            or self.y < -_EDGE_TOLERANCE
            or self.x + self.w > 1 + _EDGE_TOLERANCE
            or self.y + self.h > 1 + _EDGE_TOLERANCE
        ):
            raise ValidationError(f"영역이 화면을 벗어났습니다: {values}")

    @classmethod
    def clamped(cls, x: float, y: float, w: float, h: float) -> Region | None:
        """화면 밖으로 나간 부분을 잘라 낸 영역. 남는 것이 없으면 None (모델이 준 박스를 받을 때 쓴다)."""
        try:
            fx, fy, fw, fh = float(x), float(y), float(w), float(h)
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(v) for v in (fx, fy, fw, fh)):
            return None  # max(0.0, nan) 은 0.0 이라, 걸러 내지 않으면 엉뚱한 영역이 된다
        left, top = max(0.0, fx), max(0.0, fy)
        right, bottom = min(1.0, fx + fw), min(1.0, fy + fh)
        if right - left <= 0 or bottom - top <= 0:
            return None
        return cls(left, top, right - left, bottom - top)

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)

    @property
    def anchor(self) -> str:
        """세로 위치를 셋으로 나눈 이름: top / middle / bottom."""
        cy = self.center[1]
        return "top" if cy < 1 / 3 else ("bottom" if cy > 2 / 3 else "middle")

    def padded(self, ratio: float) -> Region:
        """가로·세로를 ratio 만큼 넓히고 화면 안으로 자른다."""
        dx, dy = self.w * ratio, self.h * ratio
        grown = Region.clamped(self.x - dx, self.y - dy, self.w + 2 * dx, self.h + 2 * dy)
        return grown or self

    def iou(self, other: Region) -> float:
        """두 영역이 겹치는 정도 (교집합 ÷ 합집합)."""
        left, top = max(self.x, other.x), max(self.y, other.y)
        right = min(self.x + self.w, other.x + other.w)
        bottom = min(self.y + self.h, other.y + other.h)
        overlap = max(0.0, right - left) * max(0.0, bottom - top)
        union = self.area + other.area - overlap
        return overlap / union if union > 0 else 0.0

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Region:
        return cls(float(data["x"]), float(data["y"]), float(data["w"]), float(data["h"]))


def region_or_none(data: Mapping[str, Any] | None) -> Region | None:
    return Region.from_dict(data) if data else None
