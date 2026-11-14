"""테마의 스타일: 모든 장면에 걸리는 장면 수준 조건, 주된 색, 구도·질감 메모 (ADR-0025)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .conditions import Condition, _conditions


@dataclass(frozen=True)
class StyleSpec:
    """스타일: 모든 장면에 거는 장면 수준 조건과, 조건으로 옮기기 어려운 겉모습의 설명."""

    conditions: tuple[Condition, ...] = ()  # lighting, mood, time_of_day, style …
    palette: tuple[str, ...] = ()  # 주된 색
    notes: str = ""  # 구도·질감·편집 느낌 (글)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conditions": [c.to_dict() for c in self.conditions],
            "palette": list(self.palette),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StyleSpec:
        return cls(
            conditions=_conditions(data.get("conditions")),
            palette=tuple(str(c) for c in data.get("palette", ())),
            notes=str(data.get("notes", "")),
        )
