"""테마의 형식: 장면 자리와 길이, 화면비, 원본의 컷 길이 (ADR-0025).

자리는 원본의 컷을 다시 만들 수 있는 길이로 묶은 것이다 (slot_spans_from_shots).
편의 장면은 테마의 자리 그대로다 (순서·길이·키).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from frame.domain.errors import NotFound, ValidationError
from frame.domain.series.source_analysis import Shot

from .conditions import Condition, _conditions

DEFAULT_MIN_SLOT_MS = 3000  # 장면 자리의 길이 하한 (구조화 프롬프트의 장면 하한과 같다)


SLOT_GRID_MS = 100  # 자리 길이의 단위 (30 fps 에서 프레임 수가 정확히 맞는 길이)


@dataclass(frozen=True)
class SceneSlot:
    """형식의 장면 자리 하나. 편마다 내용은 바뀌어도 순서·길이·구실은 같다."""

    slot_key: str
    duration_ms: int
    purpose: str = ""  # 이 자리의 구실 (예: 진행자가 인사한다, 제품을 가까이 보여 준다)
    cast_keys: tuple[str, ...] = ()  # 이 자리에 늘 나오는 고정 출연
    conditions: tuple[
        Condition, ...
    ] = ()  # 이 자리의 장면 수준 조건 (카메라·장소 등). 스타일의 조건을 덮는다

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_key": self.slot_key,
            "duration_ms": self.duration_ms,
            "purpose": self.purpose,
            "cast_keys": list(self.cast_keys),
            "conditions": [c.to_dict() for c in self.conditions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SceneSlot:
        return cls(
            slot_key=str(data["slot_key"]),
            duration_ms=int(data["duration_ms"]),
            purpose=str(data.get("purpose", "")),
            cast_keys=tuple(data.get("cast_keys", ())),
            conditions=_conditions(data.get("conditions")),
        )


@dataclass(frozen=True)
class FormatSpec:
    """형식: 장면 자리의 순서와 길이, 그리고 원본에서 본 컷 리듬."""

    slots: tuple[SceneSlot, ...]
    aspect_ratio: str = "9:16"
    observed_shot_ms: tuple[int, ...] = ()  # 원본의 컷 길이 (자리로 묶기 전)

    @property
    def total_duration_ms(self) -> int:
        return sum(slot.duration_ms for slot in self.slots)

    @property
    def mean_shot_ms(self) -> float | None:
        """원본의 평균 컷 길이. 컷을 재지 않았으면 자리 길이의 평균."""
        lengths = self.observed_shot_ms or tuple(slot.duration_ms for slot in self.slots)
        return sum(lengths) / len(lengths) if lengths else None

    @property
    def cuts_per_10s(self) -> float | None:
        lengths = self.observed_shot_ms or tuple(slot.duration_ms for slot in self.slots)
        total = sum(lengths)
        return (len(lengths) - 1) * 10_000 / total if total > 0 else None

    def span(self, slot_key: str) -> tuple[int, int]:
        """그 자리의 시작·끝 시각 (편의 타임라인)."""
        start = 0
        for slot in self.slots:
            if slot.slot_key == slot_key:
                return (start, start + slot.duration_ms)
            start += slot.duration_ms
        raise NotFound(f"장면 자리 '{slot_key}' 가 테마에 없습니다", slot_key=slot_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "aspect_ratio": self.aspect_ratio,
            "slots": [slot.to_dict() for slot in self.slots],
            "observed_shot_ms": list(self.observed_shot_ms),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FormatSpec:
        return cls(
            slots=tuple(SceneSlot.from_dict(s) for s in data.get("slots", ())),
            aspect_ratio=str(data.get("aspect_ratio", "9:16")),
            observed_shot_ms=tuple(int(v) for v in data.get("observed_shot_ms", ())),
        )


def slot_spans_from_shots(
    shots: Sequence[Shot],
    *,
    min_slot_ms: int = DEFAULT_MIN_SLOT_MS,
    grid_ms: int = SLOT_GRID_MS,
) -> tuple[tuple[int, int], ...]:
    """원본의 컷(샷)을 다시 만들 수 있는 장면 자리의 구간으로 묶는다.

    - 길이가 min_slot_ms 에 못 미치는 샷은 뒤의 샷과 묶는다 (마지막에 남으면 앞 자리에 붙인다).
      영상 전체가 min_slot_ms 보다 짧으면 자리 하나가 된다.
    - 자리의 경계는 grid_ms 단위로 맞춘다 (장면 길이가 프레임 수와 정확히 맞게).
    결과는 0 ms 에서 시작해 빈틈 없이 이어지는 (시작, 끝) 구간이다.
    """
    if not shots:
        raise ValidationError("샷이 하나 이상 있어야 합니다")
    if min_slot_ms <= 0 or grid_ms <= 0:
        raise ValidationError("자리 길이의 하한과 단위는 0 보다 커야 합니다")
    groups: list[list[int]] = []  # [시작, 끝]
    for shot in shots:
        if groups and groups[-1][1] - groups[-1][0] < min_slot_ms:
            groups[-1][1] = shot.end_ms
        else:
            groups.append([shot.start_ms, shot.end_ms])
    if len(groups) > 1 and groups[-1][1] - groups[-1][0] < min_slot_ms:
        tail = groups.pop()
        groups[-1][1] = tail[1]
    spans: list[tuple[int, int]] = []
    cursor = 0
    for _, end in groups:
        snapped = max(cursor + grid_ms, round(end / grid_ms) * grid_ms)
        spans.append((cursor, snapped))
        cursor = snapped
    return tuple(spans)
