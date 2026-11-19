"""VLM 에게 화면 속 객체의 박스를 물을 때 함께 쓰는 형식과 읽기 규칙 (정체성 기준 추출 · 객체 추적).

좌표는 화면에 대한 비율(0..1)이다: x·y 는 왼쪽 위, w·h 는 크기. 실측(2026-11-19)으로 VLM 의 박스는 대략의 위치이고,
작은 물건일수록 엉뚱한 곳을 짚기도 한다 — 그대로 믿지 말고 쓰는 쪽이 확인하거나(추적기의 박스 확인) 여유를 둔다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from frame.adapters.shared.media.frames import Box

__all__ = ["BOX_SCHEMA", "valid_box"]

BOX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["x", "y", "w", "h"],
    "properties": {name: {"type": "number"} for name in ("x", "y", "w", "h")},
}


def valid_box(answer: Mapping[str, Any], min_area: float) -> Box | None:
    """보인다고 답했고 박스가 화면 안의 쓸 만한 크기일 때만 Box. 나머지는 None."""
    if answer.get("visible") is not True or not isinstance(answer.get("box"), Mapping):
        return None
    raw = answer["box"]
    try:
        x, y, w, h = (float(raw[name]) for name in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0 and w > 0.0 and h > 0.0):
        return None
    w, h = min(w, 1.0 - x), min(h, 1.0 - y)
    return Box(x, y, w, h) if w * h >= min_area else None
