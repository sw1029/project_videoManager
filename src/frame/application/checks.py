"""CheckSuite (Composite): 여러 Checker 를 하나처럼 실행한다.

검수는 유료 생성이 끝난 뒤에 돈다. 검수기 하나가 예상하지 못한 오류로 끝나도 버전을 실패로 만들지 않고 다른
검수기의 결과도 버리지 않는다. 그 검수기의 판정을 미판정으로 남기고 나머지를 계속한다
(포트 계약: 판정할 수 없으면 FAIL 이 아니라 UNDETERMINED).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from frame.domain.enums import CheckType, Verdict

from .ports.checker import CheckContext, Checker, CheckOutcome

log = logging.getLogger(__name__)

CRASHED_REASON = "검수기가 예상하지 못한 오류로 끝나 판정하지 못했습니다"


class CheckSuite:
    def __init__(self, checkers: Sequence[Checker]) -> None:
        self._checkers = tuple(checkers)

    @property
    def check_types(self) -> tuple[CheckType, ...]:
        return tuple(c.check_type for c in self._checkers)

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        outcomes: list[CheckOutcome] = []
        for checker in self._checkers:
            try:
                produced = list(checker.check(ctx))
            except Exception as exc:  # noqa: BLE001 - 마지막 방어선: 검수기의 오류로 과금된 버전을 잃지 않는다
                log.exception(
                    "검수기 %s 가 예상하지 못한 오류로 끝났습니다 (버전 %s). 그 판정은 미판정으로 남깁니다",
                    type(checker).__name__,
                    ctx.version.version_id,
                )
                produced = [_crashed(checker, exc)]
            outcomes.extend(produced)
        return outcomes


def _crashed(checker: Checker, exc: Exception) -> CheckOutcome:
    """오류로 끝난 검수기의 자리에 남기는 결과.

    오류의 원문은 싣지 않는다 (경로·주소가 들어 있을 수 있다). 원문은 서버 로그에만 남긴다.
    """
    return CheckOutcome(
        check_type=checker.check_type,
        verdict=Verdict.UNDETERMINED,
        detail={
            "reason": CRASHED_REASON,
            "checker": type(checker).__name__,
            "error": type(exc).__name__,
        },
    )
