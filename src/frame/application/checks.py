"""CheckSuite (Composite): 여러 Checker 를 하나처럼 실행한다."""

from __future__ import annotations

from collections.abc import Sequence

from frame.domain.enums import CheckType

from .ports.checker import CheckContext, Checker, CheckOutcome


class CheckSuite:
    def __init__(self, checkers: Sequence[Checker]) -> None:
        self._checkers = tuple(checkers)

    @property
    def check_types(self) -> tuple[CheckType, ...]:
        return tuple(c.check_type for c in self._checkers)

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        outcomes: list[CheckOutcome] = []
        for checker in self._checkers:
            outcomes.extend(checker.check(ctx))
        return outcomes
