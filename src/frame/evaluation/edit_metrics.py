"""지표 1 '프롬프트 변화에 따른 객체 변화 민감도' 계산 (순수 함수).

- 수정 성공: 변경된 장면에서 (대상 객체, 속성) 의 REQUIREMENT 판정이 모두 PASS
  - 판정할 요구조건 키(target_requirement_keys)를 주면 키로 찾는다. 결과가 없는 키도, 미판정도 PASS 가 아니다.
  - 주지 않으면 detail 의 predicate 로 찾는다 (예전 방식). 장면 전체를 판정하지 못한 결과(detail 에 predicate 없음)가
    빠지므로, 실제 검수기 결과에는 키를 준다 (ADR-0022).
- 비대상 변경률: 1 - PRESERVATION 점수 평균 (판정 자료가 없으면 None = 미판정, 성공으로 집계하지 않음)
  - 보존 검수기를 둘 이상 켜면 측정 기준(detail.method)별로 나눠 잰다: select_preservation (ADR-0018 · ADR-0022)
목표: 성공률 >= 80% (SUCCESS_RATE_TARGET), 비대상 변경률 <= 10% (NON_TARGET_CHANGE_TARGET)
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from frame.domain.checks import (
    PRESERVATION_BY_KEEP,
    PRESERVATION_BY_SSIM,
    PRESERVATION_REUSED,
    CheckResult,
)
from frame.domain.enums import CheckType, Verdict
from frame.domain.metrics import preservation_rate

# 결과에 남기는 수의 자릿수. 기록 형식의 일부라 실행기 · 보고서가 같은 값을 쓴다
RATE_DIGITS = 6  # 비율 · 비용
SECONDS_DIGITS = 3  # 걸린 시간 (초)

# 지표 1 목표 (T12). 케이스 판정(evaluate_case)과 전체 판정(EditEvaluation)이 함께 쓴다
SUCCESS_RATE_TARGET = 0.8  # 전체: 성공한 케이스의 비율 >= 80%
NON_TARGET_CHANGE_TARGET = 0.1  # 케이스: 비대상 변경률 <= 10% 여야 성공, 전체: 평균 <= 10%

# 비대상 변경률의 측정 기준 = PRESERVATION 결과의 detail.method (이름은 domain/checks.py 의 것이다)
# - vlm_keep: vlm_preservation 의 유지 요소 판정 (요소 하나가 결과 하나, 그대로 1.0 / 달라짐 0.0)
# - ssim: ssim_preservation 의 부모 클립 대비 SSIM (다시 만든 장면 하나가 결과 하나, 화면 전체)
PRESERVATION_BASES: tuple[str, ...] = (PRESERVATION_BY_KEEP, PRESERVATION_BY_SSIM)
# 부모 클립을 그대로 쓴 장면. 어느 보존 검수기든 1.0 이고, 두 검수기를 함께 켜면 장면마다 하나씩 나온다
REUSED_METHOD = PRESERVATION_REUSED


@dataclass(frozen=True)
class EditCase:
    case_id: str
    target_entity_key: str | None  # None = 장면 수준 조건
    predicate: str
    changed_scene_keys: frozenset[str]
    revision_results: Sequence[CheckResult]
    # 판정할 요구조건 키 (수정 버전 IR 에서 바뀐 장면 × 대상 객체 × 술어). None 이면 detail 의 predicate 로 찾는다
    target_requirement_keys: frozenset[str] | None = None


@dataclass(frozen=True)
class EditCaseOutcome:
    case_id: str
    success: bool
    non_target_change_rate: float | None
    target_met: bool = False  # 대상 요구조건이 모두 PASS 인가 (실패 사유를 가르는 근거)


@dataclass(frozen=True)
class EditEvaluation:
    cases: int
    success_rate: float
    mean_non_target_change_rate: float | None
    success_target: float = SUCCESS_RATE_TARGET
    change_target: float = NON_TARGET_CHANGE_TARGET

    @property
    def meets_target(self) -> bool:
        return (
            self.cases > 0
            and self.success_rate >= self.success_target
            and self.mean_non_target_change_rate is not None
            and self.mean_non_target_change_rate <= self.change_target
        )


def evaluate_case(case: EditCase) -> EditCaseOutcome:
    target_met = _target_met(case)
    preservation = preservation_rate(case.revision_results)
    change_rate = None if preservation is None else round(1.0 - preservation, RATE_DIGITS)
    success = target_met and change_rate is not None and change_rate <= NON_TARGET_CHANGE_TARGET
    return EditCaseOutcome(case.case_id, success, change_rate, target_met)


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


def select_preservation(results: Iterable[CheckResult], basis: str) -> list[CheckResult]:
    """PRESERVATION 결과를 한 측정 기준의 것만 남긴다. 다른 종류의 결과는 순서 그대로 둔다.

    - 그 기준(detail.method)의 결과와, 부모 클립을 그대로 쓴 장면의 결과(method=identical, 장면당 하나)를 남긴다.
    - method 를 남기지 않는 검수기(Fake 등)의 결과는 어느 기준인지 알 수 없어 모든 기준에 넣는다.
    - 그 기준의 결과가 하나도 없으면(그 검수기를 켜지 않음) PRESERVATION 결과를 모두 뺀다.
      재사용 장면의 1.0 만 남으면 다시 만든 장면을 보지 않고 '변경 없음' 이 되기 때문이다 (→ 미판정).
    """
    if basis not in PRESERVATION_BASES:
        raise ValueError(f"알 수 없는 측정 기준: {basis} (가능: {', '.join(PRESERVATION_BASES)})")
    items = list(results)
    methods = {_method(r) for r in items if r.check_type is CheckType.PRESERVATION}
    measured = basis in methods or None in methods
    selected: list[CheckResult] = []
    reused_scenes: set[str | None] = set()
    for result in items:
        if result.check_type is not CheckType.PRESERVATION:
            selected.append(result)
            continue
        if not measured:
            continue
        method = _method(result)
        if method == basis or method is None:
            selected.append(result)
        elif method == REUSED_METHOD and result.scene_key not in reused_scenes:
            reused_scenes.add(result.scene_key)
            selected.append(result)
    return selected


def _method(result: CheckResult) -> str | None:
    method = result.detail.get("method")
    return method if isinstance(method, str) and method else None


def _target_met(case: EditCase) -> bool:
    requirements = [r for r in case.revision_results if r.check_type is CheckType.REQUIREMENT]
    if case.target_requirement_keys is None:
        target = [
            r
            for r in requirements
            if r.entity_key == case.target_entity_key
            and r.detail.get("predicate") == case.predicate
            and r.scene_key in case.changed_scene_keys
        ]
        return bool(target) and all(r.verdict is Verdict.PASS for r in target)
    if not case.target_requirement_keys:
        return False
    verdicts: dict[str, list[Verdict]] = {}
    for result in requirements:
        if result.requirement_key is not None:
            verdicts.setdefault(result.requirement_key, []).append(result.verdict)
    return all(
        verdicts.get(key) and all(v is Verdict.PASS for v in verdicts[key])
        for key in case.target_requirement_keys
    )
