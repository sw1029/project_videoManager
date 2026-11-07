"""지표 1 '프롬프트 변화에 따른 객체 변화 민감도' 계산 (순수 함수).

- 수정 성공: 변경된 장면에서 (대상 객체, 속성) 의 REQUIREMENT 판정이 모두 PASS
- 비대상 변경률: 1 - PRESERVATION 점수 평균 (판정 자료가 없으면 None = 미판정, 성공으로 집계하지 않음)
목표: 성공률 >= 80%, 비대상 변경률 <= 10%
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from frame.domain.checks import CheckResult
from frame.domain.enums import CheckType, Verdict
from frame.domain.metrics import preservation_rate


@dataclass(frozen=True)
class EditCase:
    case_id: str
    target_entity_key: str
    predicate: str
    changed_scene_keys: frozenset[str]
    revision_results: Sequence[CheckResult]


@dataclass(frozen=True)
class EditCaseOutcome:
    case_id: str
    success: bool
    non_target_change_rate: float | None


@dataclass(frozen=True)
class EditEvaluation:
    cases: int
    success_rate: float
    mean_non_target_change_rate: float | None
    success_target: float = 0.8
    change_target: float = 0.1

    @property
    def meets_target(self) -> bool:
        return (
            self.cases > 0
            and self.success_rate >= self.success_target
            and self.mean_non_target_change_rate is not None
            and self.mean_non_target_change_rate <= self.change_target
        )


def evaluate_case(case: EditCase) -> EditCaseOutcome:
    target = [
        r
        for r in case.revision_results
        if r.check_type is CheckType.REQUIREMENT
        and r.entity_key == case.target_entity_key
        and r.detail.get("predicate") == case.predicate
        and r.scene_key in case.changed_scene_keys
    ]
    preservation = preservation_rate(case.revision_results)
    change_rate = None if preservation is None else round(1.0 - preservation, 6)
    success = bool(target) and all(r.verdict is Verdict.PASS for r in target)
    if change_rate is None or change_rate > 0.1:
        success = False
    return EditCaseOutcome(case.case_id, success, change_rate)


def evaluate_cases(cases: Iterable[EditCase]) -> EditEvaluation:
    outcomes = [evaluate_case(c) for c in cases]
    if not outcomes:
        return EditEvaluation(cases=0, success_rate=0.0, mean_non_target_change_rate=None)
    rates = [o.non_target_change_rate for o in outcomes if o.non_target_change_rate is not None]
    return EditEvaluation(
        cases=len(outcomes),
        success_rate=sum(1 for o in outcomes if o.success) / len(outcomes),
        mean_non_target_change_rate=(sum(rates) / len(rates)) if rates else None,
    )
