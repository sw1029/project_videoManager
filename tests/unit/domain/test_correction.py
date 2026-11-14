"""화면 정보의 어긋남: 검수 결과를 '고칠 곳' 으로 옮기는 규칙과 문구 비교 (ADR-0027)."""

from __future__ import annotations

from dataclasses import replace

from frame.domain.checks import CheckResult, WarningInterval
from frame.domain.correction import (
    CorrectionMethod,
    IssueKind,
    ScreenIssue,
    is_text_requirement,
    issues_from_checks,
    normalize_screen_text,
    patch_overlay_id,
    screen_text_matches,
)
from frame.domain.enums import CheckType, Verdict
from frame.domain.ir import RequirementSpec
from frame.domain.screen import Region


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
