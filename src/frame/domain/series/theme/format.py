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
SPEECH_EDGE_MS = 150  # 말한 줄의 처음·끝에서 이만큼 안쪽이어야 '문장 중간' 이다 (낱말 시각의 오차)
MIN_PAUSE_MS = 250  # 줄과 줄 사이가 이만큼 비어야 자리를 나눌 수 있는 말 멈춤이다


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
    speech: Sequence[tuple[int, int]] = (),
    max_slot_ms: int | None = None,
) -> tuple[tuple[int, int], ...]:
    """원본의 컷(샷)을 다시 만들 수 있는 장면 자리의 구간으로 묶는다.

    - 자리는 min_slot_ms 보다 짧지 않다 (영상 전체가 그보다 짧으면 자리 하나다). 그 안에서 자리를 가장 많이 만든다.
    - speech (말한 줄의 구간)를 주면, 자리 수가 같은 묶음 가운데 문장 중간에 놓이는 경계가 가장 적은 것을 고른다.
      짧은 컷을 앞에 붙일지 뒤에 붙일지를 말이 정한다. 말 때문에 자리가 줄지는 않는다
      (내레이션은 컷 위로 이어지는 것이 보통이다).
    - 그래도 같으면 앞쪽 경계가 이른 묶음이다 (짧은 컷은 뒤의 컷과 묶인다).
    - max_slot_ms 를 주면 그보다 긴 자리를 나눈다: 말 멈춤이 있으면 거기서, 없으면 고르게.
      컷이 없는 긴 영상을 생성 모델이 만들 수 있는 길이로 나눌 때 쓴다.
    - 자리의 경계는 grid_ms 단위로 맞춘다 (장면 길이가 프레임 수와 정확히 맞게).
    결과는 0 ms 에서 시작해 빈틈 없이 이어지는 (시작, 끝) 구간이다.
    """
    if not shots:
        raise ValidationError("샷이 하나 이상 있어야 합니다")
    if min_slot_ms <= 0 or grid_ms <= 0:
        raise ValidationError("자리 길이의 하한과 단위는 0 보다 커야 합니다")
    if max_slot_ms is not None and max_slot_ms < min_slot_ms:
        raise ValidationError("자리 길이의 상한은 하한보다 짧을 수 없습니다")
    spoken = sorted((a, b) for a, b in speech if b > a)
    ends = _group_ends([shots[0].start_ms, *(shot.end_ms for shot in shots)], min_slot_ms, spoken)
    if max_slot_ms is not None:
        ends = _split_long(shots[0].start_ms, ends, min_slot_ms, max_slot_ms, _pauses(spoken))
    spans: list[tuple[int, int]] = []
    cursor = 0
    for end in ends:
        snapped = max(cursor + grid_ms, round(end / grid_ms) * grid_ms)
        spans.append((cursor, snapped))
        cursor = snapped
    return tuple(spans)


def _group_ends(
    points: Sequence[int], min_slot_ms: int, spoken: Sequence[tuple[int, int]]
) -> list[int]:
    """컷의 자리(points: 시작과 샷의 끝들)에서 자리의 끝을 고른다.

    좋은 묶음의 순서: 자리가 많은 것 > 문장 중간의 경계가 적은 것 > 앞쪽 경계가 이른 것.
    """
    last = len(points) - 1
    if points[last] - points[0] < min_slot_ms:
        return [points[last]]
    # best[j]: points[j] 에서 끝나는 가장 좋은 묶음의 (자리 수, -문장 중간의 경계 수, 경계의 음수들)
    best: list[tuple[int, int, tuple[int, ...]] | None] = [None] * (last + 1)
    best[0] = (0, 0, ())
    for j in range(1, last + 1):
        cut_in_sentence = j < last and _inside(points[j], spoken)
        for i in range(j):
            before = best[i]
            if before is None or points[j] - points[i] < min_slot_ms:
                continue
            candidate = (before[0] + 1, before[1] - cut_in_sentence, (*before[2], -points[j]))
            current = best[j]
            if current is None or candidate > current:
                best[j] = candidate
    # 전체가 min_slot_ms 이상이므로 자리 하나짜리 묶음은 늘 있다 (처음부터 끝까지)
    chosen = best[last] or (1, 0, (-points[last],))
    return [-point for point in chosen[2]]


def _inside(point: int, spoken: Sequence[tuple[int, int]]) -> bool:
    """그 시각이 말한 줄의 한가운데인가. 줄의 처음·끝에 바짝 붙은 시각은 가운데로 보지 않는다."""
    return any(a + SPEECH_EDGE_MS < point < b - SPEECH_EDGE_MS for a, b in spoken)


def _pauses(spoken: Sequence[tuple[int, int]]) -> list[int]:
    """말 멈춤의 가운데 시각들 (줄과 줄 사이가 MIN_PAUSE_MS 이상 빈 곳)."""
    return [
        (previous[1] + following[0]) // 2
        for previous, following in zip(spoken, spoken[1:], strict=False)
        if following[0] - previous[1] >= MIN_PAUSE_MS
    ]


def _split_long(
    start: int, ends: Sequence[int], min_slot_ms: int, max_slot_ms: int, pauses: Sequence[int]
) -> list[int]:
    result: list[int] = []
    for end in ends:
        length = end - start
        parts = -(-length // max_slot_ms)  # 올림
        previous = start
        for number in range(1, parts):
            target = start + round(length * number / parts)
            reach = length / parts / 2  # 이보다 먼 멈춤은 쓰지 않는다 (조각의 길이가 너무 달라진다)
            near = [p for p in pauses if abs(p - target) <= reach]
            cut = min(near, key=lambda p: abs(p - target)) if near else target
            if cut - previous >= min_slot_ms and end - cut >= min_slot_ms:
                result.append(cut)
                previous = cut
        result.append(end)
        start = end
    return result
