"""지표 1 평가 하네스: 수정 성공률 >= 80%, 비대상 변경률 <= 10%."""

import pytest

from frame.domain.checks import CheckResult
from frame.domain.enums import CheckType, Verdict
from frame.evaluation import (
    PRESERVATION_BASES,
    EditCase,
    evaluate_case,
    evaluate_cases,
    select_preservation,
)


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


# ---- 판정할 요구조건을 키로 찾기 (수정 버전 IR 의 바뀐 장면 × 대상 객체 × 술어) ----


def _keyed_case(results, keys=("intro:cup:color", "closeup:cup:color")):
    preservation = [_pres("intro", 1.0), _pres("closeup", 1.0), _pres("outro", 1.0)]
    return EditCase(
        "k1",
        "cup",
        "color",
        frozenset({"intro", "closeup"}),
        [*results, *preservation],
        target_requirement_keys=frozenset(keys),
    )


def _undetermined_without_predicate(scene):
    """vlm_requirement 가 장면 전체를 판정하지 못했을 때의 결과: detail 에 predicate 가 없다."""
    return CheckResult(
        f"chk_u_{scene}",
        "ver_r",
        CheckType.REQUIREMENT,
        Verdict.UNDETERMINED,
        entity_key="cup",
        scene_key=scene,
        requirement_key=f"{scene}:cup:color",
        detail={"method": "vlm", "reason": "ffmpeg 실패"},
    )


def test_keyed_case_succeeds_when_every_target_requirement_passes():
    outcome = evaluate_case(
        _keyed_case([_req("intro", Verdict.PASS), _req("closeup", Verdict.PASS)])
    )
    assert outcome.success and outcome.target_met


def test_keyed_case_counts_undetermined_result_without_predicate_as_not_met():
    """predicate 로만 찾으면 이 결과가 빠져 나머지 장면의 PASS 만으로 성공이 된다."""
    outcome = evaluate_case(
        _keyed_case([_req("intro", Verdict.PASS), _undetermined_without_predicate("closeup")])
    )
    assert not outcome.success and not outcome.target_met


def test_keyed_case_with_missing_result_is_not_met():
    outcome = evaluate_case(_keyed_case([_req("intro", Verdict.PASS)]))
    assert not outcome.success and not outcome.target_met


def test_keyed_case_without_target_keys_is_not_met():
    outcome = evaluate_case(_keyed_case([_req("intro", Verdict.PASS)], keys=()))
    assert not outcome.target_met


def test_outcome_reports_target_met_separately_from_change_rate():
    outcome = evaluate_case(_case("c5", preservation=(0.5, 0.5, 0.5)))
    assert outcome.target_met and not outcome.success


# ---- 비대상 변경률의 측정 기준 (detail.method 별로 나눈다, ADR-0018 · 연구 문서 B7) ----


def _p(scene, score, method=None, verdict=None, entity=None):
    detail = {} if method is None else {"method": method}
    return CheckResult(
        f"chk_{method}_{scene}_{entity}",
        "ver_r",
        CheckType.PRESERVATION,
        verdict or (Verdict.PASS if (score or 0) >= 0.9 else Verdict.FAIL),
        score=score,
        scene_key=scene,
        entity_key=entity,
        detail=detail,
    )


def _both_checkers():
    """vlm_preservation 과 ssim_preservation 을 함께 켠 수정 버전 (outro 는 부모 클립 재사용)."""
    return [
        _req("intro", Verdict.PASS),
        _p("intro", 1.0, "vlm_keep", entity="barista"),
        _p("intro", 0.0, "vlm_keep", entity=None),
        _p("outro", 1.0, "identical"),
        _p("intro", 0.6, "ssim"),
        _p("outro", 1.0, "identical"),
    ]


def test_bases_are_the_two_preservation_methods():
    assert PRESERVATION_BASES == ("vlm_keep", "ssim")


def test_select_keeps_only_that_method_and_one_reused_result_per_scene():
    results = _both_checkers()
    keep = select_preservation(results, "vlm_keep")
    ssim = select_preservation(results, "ssim")
    pres = CheckType.PRESERVATION
    assert [r.detail["method"] for r in keep if r.check_type is pres] == [
        "vlm_keep",
        "vlm_keep",
        "identical",
    ]
    assert [r.detail["method"] for r in ssim if r.check_type is pres] == ["identical", "ssim"]
    # 다른 종류의 결과는 그대로 둔다
    assert [r.check_type for r in keep if r.check_type is not pres] == [CheckType.REQUIREMENT]


def test_each_basis_gives_its_own_change_rate():
    results = _both_checkers()

    def rate(basis):
        case = EditCase(
            "b", "cup", "color", frozenset({"intro"}), select_preservation(results, basis)
        )
        return evaluate_case(case).non_target_change_rate

    assert rate("vlm_keep") == round(1 - (1.0 + 0.0 + 1.0) / 3, 6)
    assert rate("ssim") == round(1 - (1.0 + 0.6) / 2, 6)


def test_basis_without_its_own_results_has_no_preservation_data():
    """그 기준의 검수기를 켜지 않았으면 재사용 장면의 1.0 만으로 '변경 없음' 이 되지 않게 한다."""
    results = [
        _req("intro", Verdict.PASS),
        _p("intro", 1.0, "vlm_keep"),
        _p("outro", 1.0, "identical"),
    ]
    selected = select_preservation(results, "ssim")
    assert [r for r in selected if r.check_type is CheckType.PRESERVATION] == []
    case = EditCase("b", "cup", "color", frozenset({"intro"}), selected)
    assert evaluate_case(case).non_target_change_rate is None


def test_results_without_method_count_for_every_basis():
    """Fake 처럼 method 를 남기지 않는 검수기의 결과는 어느 기준인지 알 수 없어 모든 기준에 넣는다."""
    results = [_p("intro", 0.95), _p("outro", 1.0)]
    for basis in PRESERVATION_BASES:
        assert select_preservation(results, basis) == results


def test_unknown_basis_is_rejected():
    with pytest.raises(ValueError):
        select_preservation([], "mse")
