"""CheckSuite (Composite): 검수기 여럿을 차례로 돌린다.

검수는 유료 생성이 끝난 뒤에 돈다. 검수기 하나가 예상 밖 오류로 끝났다고 버전을 실패로 만들거나 다른 검수기의
결과를 버리지 않는다. 그 검수기의 판정을 '미판정' 으로 남기고 나머지를 계속한다 (감사 A-13).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from frame.application.checks import CheckSuite
from frame.application.errors import LLMTimeout, StorageError
from frame.application.ports.checker import CheckContext, Checker, CheckOutcome
from frame.domain.enums import CheckType, Verdict, VersionState
from frame.domain.errors import IntegrityViolation
from frame.domain.models import VideoVersion


class Passing(Checker):
    check_type = CheckType.CONSISTENCY

    def __init__(self, entity_key: str) -> None:
        self.entity_key = entity_key

    def check(self, ctx):
        return [CheckOutcome(self.check_type, Verdict.PASS, score=1.0, entity_key=self.entity_key)]


class Crashing(Checker):
    check_type = CheckType.REQUIREMENT

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def check(self, ctx):
        raise self.error


@pytest.fixture
def ctx(ir):
    version = VideoVersion(
        "ver_1", "prj_1", VersionState.PROCESSING, datetime(2026, 1, 1, tzinfo=UTC), ir=ir
    )
    return CheckContext(version=version, ir=ir, clips={}, tracks=())


def test_the_suite_runs_every_checker_in_order(ctx):
    suite = CheckSuite([Passing("barista"), Passing("cup")])

    outcomes = suite.run(ctx)

    assert [o.entity_key for o in outcomes] == ["barista", "cup"]
    assert suite.check_types == (CheckType.CONSISTENCY, CheckType.CONSISTENCY)


def test_a_checker_that_crashes_does_not_take_the_others_down(ctx, caplog):
    suite = CheckSuite(
        [Passing("barista"), Crashing(RuntimeError("boom at /srv/frame/secret")), Passing("cup")]
    )

    with caplog.at_level(logging.ERROR, logger="frame.application.checks"):
        outcomes = suite.run(ctx)

    assert [o.verdict for o in outcomes] == [Verdict.PASS, Verdict.UNDETERMINED, Verdict.PASS]
    crashed = outcomes[1]
    assert crashed.check_type is CheckType.REQUIREMENT
    assert (crashed.requirement_key, crashed.scene_key, crashed.entity_key) == (None, None, None)
    assert crashed.score is None
    assert crashed.detail["checker"] == "Crashing" and crashed.detail["error"] == "RuntimeError"
    assert "판정하지 못했습니다" in crashed.detail["reason"]
    # 오류의 원문(경로 등)은 검수 결과에 싣지 않는다. 서버 로그에만 남긴다
    assert "secret" not in json.dumps(crashed.detail, ensure_ascii=False)
    assert "boom at /srv/frame/secret" in caplog.text


@pytest.mark.parametrize(
    "error",
    [
        StorageError("클립을 읽지 못함"),
        LLMTimeout("응답 없음"),
        IntegrityViolation("x"),
        KeyError("k"),
    ],
    ids=lambda error: type(error).__name__,
)
def test_whatever_stops_a_checker_is_recorded_as_undetermined(ctx, error):
    outcomes = CheckSuite([Crashing(error), Passing("cup")]).run(ctx)

    assert [o.verdict for o in outcomes] == [Verdict.UNDETERMINED, Verdict.PASS]
    assert outcomes[0].detail["error"] == type(error).__name__


def test_stopping_the_process_is_not_mistaken_for_a_checker_failure(ctx):
    with pytest.raises(KeyboardInterrupt):
        CheckSuite([Crashing(KeyboardInterrupt())]).run(ctx)
