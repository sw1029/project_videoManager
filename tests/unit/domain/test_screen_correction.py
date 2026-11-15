"""화면 국소 수정의 순수 함수 (ADR-0027): 검수 결과·계획에서 어긋난 곳을 찾는 규칙, 문구 비교, 계획을 고치는 걸음."""

from __future__ import annotations

from dataclasses import replace

from frame.domain.checks import CheckResult, WarningInterval
from frame.domain.enums import CheckType, Verdict
from frame.domain.ir import RequirementSpec
from frame.domain.screen import Region
from frame.domain.series.episode import OverlayKind, build_episode_plan
from frame.domain.series.screen_correction import (
    CorrectionMethod,
    IssueKind,
    PatchRegion,
    ScreenIssue,
    StepStatus,
    expected_overlay_texts,
    fix_overlay_text,
    is_text_requirement,
    issues_from_checks,
    normalize_screen_text,
    patch_overlay_id,
    patch_scene_text,
    plan_text_issues,
    repair_axis,
    screen_text_matches,
    without_patched,
)
from tests.support.themes import cafe_theme, matcha_brief, matcha_content


def _result(key, verdict=Verdict.FAIL, warnings=(), observed="", check_type=CheckType.REQUIREMENT):
    return CheckResult(
        check_id=f"chk_{key}",
        version_id="ver_1",
        check_type=check_type,
        verdict=verdict,
        score=0.0 if verdict is Verdict.FAIL else 1.0,
        requirement_key=key,
        warnings=tuple(warnings),
        detail={"observed": observed} if observed else {},
    )


def _ir_with_text(ir):
    """컵에 문구 요구조건을 하나 더한 카페 IR."""
    closeup = ir.scene("closeup")
    text = RequirementSpec("closeup", "text", "FRAME", "cup")
    return ir.with_scene(replace(closeup, requirements=closeup.requirements + (text,)))


def test_failed_attribute_check_becomes_an_issue_over_its_warning_span(ir):
    warning = WarningInterval("intro", 1333, 4000, "cup color: 요구 'red', 화면: blue mug")
    issues = issues_from_checks(
        ir, [_result("intro:cup:color", warnings=[warning], observed="blue mug")]
    )

    assert len(issues) == 1
    issue = issues[0]
    assert (issue.kind, issue.method) == (IssueKind.ATTRIBUTE, CorrectionMethod.CLIP_EDIT)
    assert (issue.scene_key, issue.start_ms, issue.end_ms) == ("intro", 1333, 4000)
    assert (issue.expected, issue.observed) == ("red", "blue mug")
    assert (issue.entity_key, issue.predicate) == ("cup", "color")
    assert (
        issue.issue_id == "requirement:intro:cup:color"
        and issue.requirement_key == "intro:cup:color"
    )
    assert issue.detail["check_id"] == "chk_intro:cup:color"


def test_issue_without_warnings_covers_the_whole_scene(ir):
    issues = issues_from_checks(ir, [_result("closeup:cup:color")])

    assert [(i.scene_key, i.start_ms, i.end_ms) for i in issues] == [("closeup", 4000, 8000)]


def test_failed_text_requirement_is_a_scene_text_issue(ir):
    with_text = _ir_with_text(ir)
    issues = issues_from_checks(with_text, [_result("closeup:cup:text", observed="FRMAE")])

    assert [i.kind for i in issues] == [IssueKind.SCENE_TEXT]
    assert (issues[0].expected, issues[0].observed) == ("FRAME", "FRMAE")
    assert is_text_requirement(RequirementSpec("closeup", "brand", "FRAME", "cup"))
    assert not is_text_requirement(RequirementSpec("closeup", "color", "red", "cup"))


def test_only_failed_requirement_checks_count(ir):
    results = [
        _result("intro:cup:color", Verdict.PASS),
        _result("closeup:cup:color", Verdict.UNDETERMINED),
        _result("intro:cup:color", Verdict.FAIL, check_type=CheckType.CONSISTENCY),
        _result("gone:cup:color", Verdict.FAIL),  # 이 IR 에 없는 요구조건
    ]

    assert issues_from_checks(ir, results) == ()


def test_issues_follow_scene_and_requirement_order(ir):
    results = [_result("outro:barista:shirt_color"), _result("intro:barista:holds:cup")]
    issues = issues_from_checks(ir, results + [_result("intro:cup:color")])

    assert [i.requirement_key for i in issues] == [
        "intro:cup:color",
        "intro:barista:holds:cup",
        "outro:barista:shirt_color",
    ]


def test_screen_text_is_compared_exactly_except_for_spacing_and_width():
    assert screen_text_matches("말차 라떼 5,000원", "말차라떼 5,000원")
    assert screen_text_matches("FRAME ２０２６", "FRAME 2026")  # 전각 숫자
    assert not screen_text_matches("말차 라떼 5,000원", "말차 라떼 5,500원")
    assert not screen_text_matches("FRAME", "frame")  # 대소문자는 다른 문구다
    assert not screen_text_matches("말차 라떼 5,000원", "□□ □□ 5,000□")
    assert not screen_text_matches("오늘의 메뉴", "")
    assert normalize_screen_text("  오늘의   메뉴 ") == "오늘의메뉴"


def test_issue_round_trips():
    issue = ScreenIssue(
        "overlay:closeup:price",
        IssueKind.OVERLAY_TEXT,
        "closeup",
        8500,
        11500,
        "말차 라떼 5,000원",
        "말차 라떼 5,O00원",
        CorrectionMethod.OVERLAY_EDIT,
        Region(0.1, 0.72, 0.8, 0.08),
        overlay_id="closeup:price",
        detail={"read_at_ms": 10000},
    )

    assert ScreenIssue.from_dict(issue.to_dict()) == issue


def test_one_patch_overlay_per_requirement():
    """가린 문구의 id 는 요구조건(장면·객체·술어)으로 정해진다. 검수가 그 id 로 고친 것을 알아본다."""
    assert patch_overlay_id("closeup", "mug", "text") == "closeup:patch:mug:text"
    assert patch_overlay_id("closeup", None, "text") == "closeup:patch:scene:text"


# ---- 같은 객체가 장면마다 다르게 보임 ----
def _inconsistent(entity_key="cup", warnings=(), verdict=Verdict.FAIL):
    return CheckResult(
        check_id=f"chk_consistency_{entity_key}",
        version_id="ver_1",
        check_type=CheckType.CONSISTENCY,
        verdict=verdict,
        score=0.5,
        entity_key=entity_key,
        warnings=tuple(warnings),
        detail={"reference": "first_visible_frame"},
    )


def test_failed_consistency_check_becomes_an_issue_in_each_warned_scene(ir):
    """요구조건은 맞아도 같은 객체가 장면마다 다르면 화면이 틀린 것이다."""
    warning = WarningInterval("closeup", 4000, 8000, "cup: 기준과 다른 모습 (cup shape)")
    issues = issues_from_checks(ir, [_inconsistent(warnings=[warning])])

    assert len(issues) == 1
    issue = issues[0]
    assert issue.issue_id == "consistency:closeup:cup"
    assert (issue.kind, issue.method) == (IssueKind.CONSISTENCY, CorrectionMethod.CLIP_EDIT)
    assert (issue.scene_key, issue.start_ms, issue.end_ms) == ("closeup", 4000, 8000)
    assert issue.entity_key == "cup" and issue.predicate is None and issue.requirement_key is None
    assert "cup shape" in issue.observed and issue.expected == ""
    assert issue.detail == {
        "check_id": "chk_consistency_cup",
        "score": 0.5,
        "reference": "first_visible_frame",
    }


def test_consistency_issue_without_warnings_covers_every_scene_of_the_entity(ir):
    issues = issues_from_checks(ir, [_inconsistent()])

    assert [i.scene_key for i in issues] == [
        s.scene_key for s in ir.scenes if "cup" in s.entity_keys
    ]
    assert issues and all(i.kind is IssueKind.CONSISTENCY for i in issues)


def test_passed_or_entityless_consistency_checks_are_not_issues(ir):
    warning = WarningInterval("gone", 0, 1000, "없는 장면")
    results = [
        _inconsistent(verdict=Verdict.PASS),
        _inconsistent(verdict=Verdict.UNDETERMINED),
        _inconsistent(entity_key=None),
        _inconsistent(entity_key="ghost"),  # 이 IR 에 없는 객체
        _inconsistent(warnings=[warning]),  # 이 IR 에 없는 장면
    ]

    assert issues_from_checks(ir, results) == ()


def test_consistency_issues_come_after_the_requirement_issues_of_their_scene(ir):
    warning = WarningInterval("intro", 0, 4000, "cup: 기준과 다른 모습")
    issues = issues_from_checks(
        ir,
        [
            _inconsistent(warnings=[warning]),
            _result("closeup:cup:color"),
            _result("intro:cup:color"),
        ],
    )

    assert [i.issue_id for i in issues] == [
        "requirement:intro:cup:color",
        "consistency:intro:cup",
        "requirement:closeup:cup:color",
    ]


# ---- 계획에서 찾기 · 고치는 걸음 (저장소·모델 없이) ----
def _plan():
    return build_episode_plan(cafe_theme(), matcha_brief(), matcha_content())


def _wrong_price(plan):
    price = plan.overlay("closeup:price")
    return plan.with_overlay(replace(price, text="말차 라떼 6,000원"))


def test_plan_text_that_differs_from_the_theme_template_is_an_overlay_issue():
    theme, plan = cafe_theme(), _plan()
    assert plan_text_issues(theme, plan) == []  # 틀에 값을 넣은 그대로면 어긋난 곳이 없다

    (issue,) = plan_text_issues(theme, _wrong_price(plan))

    assert (issue.issue_id, issue.kind, issue.method) == (
        "overlay:closeup:price",
        IssueKind.OVERLAY_TEXT,
        CorrectionMethod.OVERLAY_EDIT,
    )
    assert (issue.expected, issue.observed) == ("말차 라떼 5,000원", "말차 라떼 6,000원")
    # 구간은 편의 타임라인이다: 셋째 장면(8000 ms 에 시작)의 0.5~3.5초
    assert (issue.scene_key, issue.start_ms, issue.end_ms) == ("closeup", 8500, 11500)
    assert issue.detail["source"] == "plan"


def test_slot_level_templates_have_no_expected_text():
    """자리마다 값이 다른 틀(자막)은 기대값이 없다: 그 자리의 값이 곧 내용이다."""
    expected = expected_overlay_texts(cafe_theme(), matcha_brief().variables)

    assert expected == {"greeting:title": "오늘의 메뉴", "closeup:price": "말차 라떼 5,000원"}


def test_fixing_an_overlay_text_changes_only_that_item():
    theme, plan = cafe_theme(), _plan()
    wrong = _wrong_price(plan)
    (issue,) = plan_text_issues(theme, wrong)

    fixed, step = fix_overlay_text(wrong, issue)

    assert fixed.overlays == plan.overlays and fixed.ir == plan.ir
    assert (step.status, step.overlay_id) == (StepStatus.APPLIED, "closeup:price")
    # 화면에서 다르게 읽힌 문구(계획은 맞음)는 글꼴·영역의 문제다. 고치지 않고 알린다
    unchanged, skipped = fix_overlay_text(wrong, replace(issue, detail={"source": "render"}))
    assert unchanged is wrong and skipped.status is StepStatus.SKIPPED


def test_patching_scene_text_covers_the_region_and_settles_the_issue():
    plan = _plan()
    issue = ScreenIssue(
        "requirement:closeup:latte:text",
        IssueKind.SCENE_TEXT,
        "closeup",
        8500,
        11000,
        expected="말차",
        observed="맏차",
        entity_key="latte",
        predicate="text",
    )
    region = Region(0.3, 0.4, 0.4, 0.1)

    patched, step = patch_scene_text(plan, issue, PatchRegion(issue.issue_id, region))

    item = patched.overlay(patch_overlay_id("closeup", "latte", "text"))
    assert (item.kind, item.text, item.region) == (OverlayKind.PATCH, "말차", region)
    assert (item.start_ms, item.end_ms) == (500, 3000)  # 장면 안의 시각으로 옮긴다
    assert (step.status, step.method) == (StepStatus.APPLIED, CorrectionMethod.PATCH)
    # 가린 뒤에는 그 요구조건을 다시 고치라고 하지 않는다. 가리기 전의 계획에서는 그대로 어긋난 곳이다
    assert without_patched(patched, [issue]) == ([], [issue.issue_id])
    assert without_patched(plan, [issue]) == ([issue], [])


def test_repair_axis_is_the_failed_requirement_or_a_look_of_the_drifting_entity():
    plan = _plan()
    failed = ScreenIssue(
        "requirement:closeup:latte:color",
        IssueKind.ATTRIBUTE,
        "closeup",
        8000,
        12000,
        expected="green",
        entity_key="latte",
        predicate="color",
    )
    drifted = ScreenIssue(
        "consistency:greeting:barista",
        IssueKind.CONSISTENCY,
        "greeting",
        0,
        4000,
        expected="",
        entity_key="barista",
    )

    assert repair_axis(plan, failed) == ("latte", "color", "green")
    # 장면마다 다르게 보인 객체에는 틀린 요구조건이 없다. 그 장면에서 그 객체의 모습 조건 하나가 축이다
    assert repair_axis(plan, drifted) == ("barista", "outfit", "white shirt and brown apron")
    assert repair_axis(plan, replace(drifted, entity_key=None)) is None
