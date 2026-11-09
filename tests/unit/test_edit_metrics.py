"""지표 1 평가 하네스: 수정 성공률 >= 80%, 비대상 변경률 <= 10%."""

from frame.domain.checks import CheckResult
from frame.domain.enums import CheckType, Verdict
from frame.evaluation import EditCase, evaluate_case, evaluate_cases


def _req(scene, verdict, entity="cup", predicate="color"):
    return CheckResult(
        f"chk_{scene}_{entity}",
        "ver_r",
        CheckType.REQUIREMENT,
        verdict,
        entity_key=entity,
        scene_key=scene,
        requirement_key=f"{scene}:{entity}:{predicate}",
        detail={"predicate": predicate},
    )


def _pres(scene, score):
    return CheckResult(
        f"chk_p_{scene}",
        "ver_r",
        CheckType.PRESERVATION,
        Verdict.PASS if score >= 0.9 else Verdict.FAIL,
        score=score,
        scene_key=scene,
    )


def _case(case_id, target_verdict=Verdict.PASS, preservation=(1.0, 0.95, 1.0)):
    results = [
        _req("intro", target_verdict),
        _req("closeup", target_verdict),
        _pres("intro", preservation[0]),
        _pres("closeup", preservation[1]),
        _pres("outro", preservation[2]),
    ]
    return EditCase(case_id, "cup", "color", frozenset({"intro", "closeup"}), results)


def test_successful_case():
    outcome = evaluate_case(_case("c1"))
    assert outcome.success
    assert outcome.non_target_change_rate == round(1 - (1.0 + 0.95 + 1.0) / 3, 6)


def test_case_fails_when_target_requirement_not_met():
    assert not evaluate_case(_case("c2", target_verdict=Verdict.FAIL)).success


def test_case_fails_when_non_target_change_exceeds_10_percent():
    assert not evaluate_case(_case("c3", preservation=(0.5, 0.5, 0.5))).success


def test_case_without_preservation_data_is_not_counted_as_success():
    case = EditCase("c4", "cup", "color", frozenset({"intro"}), [_req("intro", Verdict.PASS)])
    outcome = evaluate_case(case)
    assert not outcome.success and outcome.non_target_change_rate is None


def test_aggregate_meets_target():
    cases = [_case(f"ok{i}") for i in range(17)] + [
        _case(f"bad{i}", target_verdict=Verdict.FAIL) for i in range(3)
    ]
    evaluation = evaluate_cases(cases)
    assert evaluation.cases == 20
    assert evaluation.success_rate == 0.85
    assert evaluation.meets_target


def test_aggregate_without_cases_does_not_meet_target():
    assert not evaluate_cases([]).meets_target
