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
