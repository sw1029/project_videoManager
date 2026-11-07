"""성공 지표 계산 (순수 함수). 미판정(UNDETERMINED)은 분모에 포함하되 통과로 세지 않는다."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .checks import CheckResult
from .enums import CheckType, Verdict


@dataclass(frozen=True)
class CheckSummary:
    total: int
    passed: int
    failed: int
    undetermined: int
    requirement_reflection_rate: float | None  # 지표 2: 요구사항 반영 비율
    consistency_rate: float | None  # 지표 3: 고정 속성 유지율
    preservation_rate: float | None  # 지표 1: 비대상 보존율 (1 - 비대상 변경률)


def _rate(results: list[CheckResult]) -> float | None:
    if not results:
        return None
    scored = [r.score for r in results if r.score is not None]
    if scored and len(scored) == len(results):
        return sum(scored) / len(scored)
    return sum(1 for r in results if r.verdict is Verdict.PASS) / len(results)


def requirement_reflection_rate(results: Iterable[CheckResult]) -> float | None:
    reqs = [r for r in results if r.check_type is CheckType.REQUIREMENT]
    if not reqs:
        return None
    return sum(1 for r in reqs if r.verdict is Verdict.PASS) / len(reqs)


def consistency_rate(results: Iterable[CheckResult]) -> float | None:
    return _rate([r for r in results if r.check_type is CheckType.CONSISTENCY])


def preservation_rate(results: Iterable[CheckResult]) -> float | None:
    return _rate([r for r in results if r.check_type is CheckType.PRESERVATION])


def summarize(results: Iterable[CheckResult]) -> CheckSummary:
    items = list(results)
    return CheckSummary(
        total=len(items),
        passed=sum(1 for r in items if r.verdict is Verdict.PASS),
        failed=sum(1 for r in items if r.verdict is Verdict.FAIL),
        undetermined=sum(1 for r in items if r.verdict is Verdict.UNDETERMINED),
        requirement_reflection_rate=requirement_reflection_rate(items),
        consistency_rate=consistency_rate(items),
        preservation_rate=preservation_rate(items),
    )
