"""여러 사례의 집계와 목표 판정, 두 결과의 대표 지표 비교."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from ._scoring import CaseScore, Tally, ratio

ENTITY_RECALL_TARGET = 0.9
REQUIREMENT_F1_TARGET = 0.8
SCENE_COUNT_MATCH_TARGET = 0.8

_LOWER_IS_BETTER = frozenset({"mean_boundary_error_ms", "mean_duration_error_ms"})
_EPSILON = 1e-9


@dataclass(frozen=True)
class StructuringEvaluation:
    """여러 사례를 합친 결과. 비율은 사례의 개수를 모두 더해 계산한다 (micro 평균)."""

    cases: int
    failures: int
    entities: Tally
    requirements: Tally
    appearances: Tally
    scene_count_matches: int
    mean_boundary_error_ms: float | None
    mean_duration_error_ms: float | None
    entity_recall_target: float = ENTITY_RECALL_TARGET
    requirement_f1_target: float = REQUIREMENT_F1_TARGET
    scene_count_match_target: float = SCENE_COUNT_MATCH_TARGET

    @property
    def entity_recall(self) -> float:
        return self.entities.recall

    @property
    def entity_precision(self) -> float:
        return self.entities.precision

    @property
    def requirement_precision(self) -> float:
        return self.requirements.precision

    @property
    def requirement_recall(self) -> float:
        return self.requirements.recall

    @property
    def requirement_f1(self) -> float:
        return self.requirements.f1

    @property
    def scene_count_match_rate(self) -> float:
        return ratio(self.scene_count_matches, self.cases)

    @property
    def meets_target(self) -> bool:
        return (
            self.cases > 0
            and self.entity_recall >= self.entity_recall_target
            and self.requirement_f1 >= self.requirement_f1_target
            and self.scene_count_match_rate >= self.scene_count_match_target
        )


def summarize(scores: Iterable[CaseScore]) -> StructuringEvaluation:
    scores = list(scores)
    boundaries = [s.boundary_error_ms for s in scores if s.boundary_error_ms is not None]
    durations = [s.duration_error_ms for s in scores if s.duration_error_ms is not None]
    return StructuringEvaluation(
        cases=len(scores),
        failures=sum(1 for s in scores if s.error is not None),
        entities=sum((s.entities for s in scores), Tally()),
        requirements=sum((s.requirements for s in scores), Tally()),
        appearances=sum((s.appearances for s in scores), Tally()),
        scene_count_matches=sum(1 for s in scores if s.scene_count_match),
        mean_boundary_error_ms=_mean(boundaries),
        mean_duration_error_ms=_mean(durations),
    )


def headline(evaluation: StructuringEvaluation) -> dict[str, float | None]:
    """기록·비교에 쓰는 대표 지표 (순서 고정)."""
    return {
        "entity_recall": evaluation.entity_recall,
        "entity_precision": evaluation.entity_precision,
        "requirement_precision": evaluation.requirement_precision,
        "requirement_recall": evaluation.requirement_recall,
        "requirement_f1": evaluation.requirement_f1,
        "appearance_f1": evaluation.appearances.f1,
        "scene_count_match_rate": evaluation.scene_count_match_rate,
        "mean_boundary_error_ms": evaluation.mean_boundary_error_ms,
        "mean_duration_error_ms": evaluation.mean_duration_error_ms,
    }


@dataclass(frozen=True)
class MetricChange:
    name: str
    baseline: float | None
    candidate: float | None

    @property
    def lower_is_better(self) -> bool:
        return self.name in _LOWER_IS_BETTER

    @property
    def delta(self) -> float | None:
        if self.baseline is None or self.candidate is None:
            return None
        return self.candidate - self.baseline

    @property
    def regressed(self) -> bool:
        delta = self.delta
        if delta is None:
            return False
        return delta > _EPSILON if self.lower_is_better else delta < -_EPSILON


def compare_headlines(
    baseline: Mapping[str, float | None], candidate: Mapping[str, float | None]
) -> tuple[MetricChange, ...]:
    """두 결과의 대표 지표를 견준다 (기준의 순서, 한쪽에만 있는 지표는 변화량 None)."""
    names = [*baseline, *(n for n in candidate if n not in baseline)]
    return tuple(MetricChange(n, baseline.get(n), candidate.get(n)) for n in names)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None
