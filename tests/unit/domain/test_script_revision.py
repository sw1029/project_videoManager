"""편의 녹음 대본만 고친다 (ADR-0029): 줄과 그 줄을 따르는 자막만 바뀌고, 영상의 조건은 그대로다."""

from __future__ import annotations

from dataclasses import replace

import pytest

from frame.domain.errors import NotFound, ValidationError
from frame.domain.series.episode import build_episode_plan
from frame.domain.series.script_revision import ScriptEdit, revise_script
from tests.support.themes import cafe_theme, matcha_brief, matcha_content


def _captioned_theme():
    """자막이 말을 받아 적는 테마."""
    theme = cafe_theme()
    rules = tuple(replace(r, follows_speech=r.rule_key == "caption") for r in theme.text_rules)
    return replace(theme, text_rules=rules)


def _plan(theme=None):
    return build_episode_plan(theme or cafe_theme(), matcha_brief(), matcha_content())


def test_captions_that_follow_speech_are_the_script_lines():
    """말을 받아 적는 자막 규칙이면, 편의 자막은 기획이 준 자막 값이 아니라 그 자리의 대본 줄이다."""
    plan = _plan(_captioned_theme())

    captions = {o.scene_key: o.text for o in plan.overlays if o.rule_key == "caption"}
    assert captions == {line.scene_key: line.text for line in plan.script}
    # 말을 따르지 않는 테마에서는 기획이 준 자리별 값만 자막이 된다
    plain = {o.scene_key: o.text for o in _plan().overlays if o.rule_key == "caption"}
    assert plain == {"greeting": "오늘은 초록초록!"}


def test_revising_a_line_changes_the_line_and_its_caption_only():
    theme = _captioned_theme()
    plan = _plan(theme)

    revision = revise_script(plan, theme, [ScriptEdit("L2", "초록빛 말차 라떼예요.")])

    revised = revision.plan
    line = revised.line("L2")
    assert line.text == "초록빛 말차 라떼예요." and line.budget_ms == plan.line("L2").budget_ms
    assert line.estimated_ms == round(9 / theme.narration.pace_cps * 1000)
    assert revision.changed_lines == ("L2",) and revision.changed_overlays == ("show:caption",)
    assert revised.overlay("show:caption").text == "초록빛 말차 라떼예요."
    # 영상의 조건과 나머지 줄·문구는 그대로다
    assert revised.ir == plan.ir and revised.variables == plan.variables
    assert [x for x in revised.script if x.line_key != "L2"] == [
        x for x in plan.script if x.line_key != "L2"
    ]
    assert [o for o in revised.overlays if o.overlay_id != "show:caption"] == [
        o for o in plan.overlays if o.overlay_id != "show:caption"
    ]
    revised.validate()


def test_without_a_following_rule_only_the_line_changes():
    plan = _plan()

    revision = revise_script(plan, cafe_theme(), [ScriptEdit("L2", "초록빛 말차 라떼예요.")])

    assert revision.changed_overlays == () and revision.plan.overlays == plan.overlays
    assert revise_script(plan, None, [ScriptEdit("L2", "초록빛 말차 라떼예요.")]).changed_lines == (
        "L2",
    )


def test_a_line_that_cannot_be_said_within_its_scene_is_refused():
    plan = _plan()
    long_line = "이 문장은 사 초짜리 장면에서 사람이 읽기에는 아무리 빨리 읽어도 너무 길어서 받을 수 없습니다"

    with pytest.raises(ValidationError, match="너무 깁니다") as caught:
        revise_script(plan, cafe_theme(), [ScriptEdit("L2", long_line)])

    assert caught.value.context["line_key"] == "L2" and caught.value.context["limit_chars"] > 0


def test_unknown_empty_repeated_and_unchanged_edits():
    plan = _plan()
    theme = cafe_theme()

    with pytest.raises(NotFound):
        revise_script(plan, theme, [ScriptEdit("L9", "없는 줄")])
    with pytest.raises(ValidationError):
        revise_script(plan, theme, [ScriptEdit("L2", "   ")])
    with pytest.raises(ValidationError):
        revise_script(plan, theme, [ScriptEdit("L2", "하나"), ScriptEdit("L2", "둘")])
    same = revise_script(plan, theme, [ScriptEdit("L2", plan.line("L2").text)])
    assert same.plan == plan and same.changed_lines == ()


def test_dropping_or_changing_a_value_of_the_episode_is_warned():
    """대본만 고치면 화면 문구(가격표)는 그대로다. 말과 화면이 어긋나게 되는 수정은 알린다."""
    plan = _plan()  # 변수: menu=말차 라떼, price=5,000원. L3 = '가격은 오천 원.'
    theme = cafe_theme()

    dropped = revise_script(plan, theme, [ScriptEdit("L3", "정말 맛있어요.")])
    changed = revise_script(plan, theme, [ScriptEdit("L3", "가격은 오천오백 원.")])
    kept = revise_script(plan, theme, [ScriptEdit("L3", "단돈 5,000원입니다.")])

    assert (
        len(dropped.warnings) == 1
        and "price" in dropped.warnings[0]
        and "빠졌습니다" in dropped.warnings[0]
    )
    assert (
        len(changed.warnings) == 1
        and "5500원" in changed.warnings[0]
        and "다릅니다" in changed.warnings[0]
    )
    assert kept.warnings == ()  # 같은 값을 다르게 적었을 뿐이다
