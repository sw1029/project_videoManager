"""성공 지표 계산: 미판정(UNDETERMINED)은 통과로 집계하지 않는다."""

from itertools import count

from frame.domain.checks import CheckResult
from frame.domain.enums import CheckType, Verdict
from frame.domain.metrics import consistency_rate, requirement_reflection_rate, summarize

_ids = count(1)


def _r(check_type, verdict, score=None):
    return CheckResult(f"chk_{next(_ids)}", "ver_1", check_type, verdict, score=score)


def test_requirement_reflection_rate_counts_undetermined_in_denominator():
    results = [
        _r(CheckType.REQUIREMENT, Verdict.PASS),
        _r(CheckType.REQUIREMENT, Verdict.PASS),
        _r(CheckType.REQUIREMENT, Verdict.FAIL),
        _r(CheckType.REQUIREMENT, Verdict.UNDETERMINED),
    ]
    assert requirement_reflection_rate(results) == 0.5


def test_rate_is_none_without_results():
    assert requirement_reflection_rate([]) is None
    assert consistency_rate([_r(CheckType.REQUIREMENT, Verdict.PASS)]) is None


def test_consistency_rate_uses_scores_when_all_scored():
    results = [
        _r(CheckType.CONSISTENCY, Verdict.PASS, 1.0),
        _r(CheckType.CONSISTENCY, Verdict.FAIL, 0.5),
    ]
    assert consistency_rate(results) == 0.75


def test_summary_counts_verdicts_and_rates():
    results = [
        _r(CheckType.REQUIREMENT, Verdict.PASS, 1.0),
        _r(CheckType.REQUIREMENT, Verdict.FAIL, 0.0),
        _r(CheckType.CONSISTENCY, Verdict.UNDETERMINED),
        _r(CheckType.PRESERVATION, Verdict.PASS, 0.96),
    ]
    summary = summarize(results)
    assert (summary.total, summary.passed, summary.failed, summary.undetermined) == (4, 2, 1, 1)
    assert summary.requirement_reflection_rate == 0.5
    assert summary.consistency_rate == 0.0  # 점수 없는 미판정 -> PASS 비율로 계산
    assert summary.preservation_rate == 0.96
