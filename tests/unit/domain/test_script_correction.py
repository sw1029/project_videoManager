"""뽑은 대본의 줄을 고친 뒤, 그 줄에 걸린 것만 다시 맞춘다 (ADR-0029). 모델도 인식기도 부르지 않는다."""

from __future__ import annotations

from dataclasses import replace

from frame.domain.screen import TextRole
from frame.domain.script import LineKind, SceneSpan, build_script
from frame.domain.series.script_correction import refit_after_correction
from frame.domain.series.script_evidence import (
    Agreement,
    DiscrepancyKind,
    apply_script,
    link_script,
)
from frame.domain.series.source_analysis import ScreenText
from frame.domain.series.theme import ThemeVariable
from frame.domain.speech import SpeechSegment, Transcript
from tests.support.themes import cafe_theme

SCENES = (
    SceneSpan("greeting", 0, 4000),
    SceneSpan("show", 4000, 8000),
    SceneSpan("closeup", 8000, 12000),
    SceneSpan("outro", 12000, 16000),
)
SCREEN = (
    ScreenText("오늘의 메뉴", 0, 4000, role=TextRole.TITLE),
    ScreenText("바닐라 라떼 4,500원", 8500, 11500, role=TextRole.PRICE),
    ScreenText("안녕하세요, 오늘의 메뉴예요", 300, 2700, role=TextRole.CAPTION),
    ScreenText("매장에서 만나요!", 12400, 14100, role=TextRole.CAPTION),
)


def _script(menu_line="바닐라 라떼입니다.", price_line="가격은 사천오백 원."):
    segments = (
        SpeechSegment(400, 2600, "안녕하세요, 오늘의 메뉴예요."),
        SpeechSegment(4500, 6200, menu_line),
        SpeechSegment(8600, 10400, price_line),
        SpeechSegment(12500, 14000, "매장에서 만나요!"),
    )
    return build_script(Transcript("ko", segments), SCENES, duration_ms=16000)


def _extracted(theme, script, screen=SCREEN):
    """추출을 마친 상태: 대본을 근거로 맞춘 테마와 그 근거."""
    evidence = link_script(theme, script, screen)
    return apply_script(theme, script, evidence), evidence


def _fix(script, line_id, text):
    return script.with_line(script.line(line_id).with_text(text))


def test_correcting_a_misheard_price_resolves_the_discrepancy_and_refits_that_slot_only():
    """인식기가 '사천오백 원' 을 '오천 원' 으로 잘못 받아 적었다. 줄을 고치면 어긋남이 사라진다."""
    heard = _script(price_line="가격은 오천 원.")
    theme, evidence = _extracted(cafe_theme(), heard)
    assert [d.kind for d in evidence.discrepancies] == [DiscrepancyKind.VALUE]

    result = refit_after_correction(
        theme, heard, _fix(heard, "S3", "가격은 사천오백 원."), SCREEN, evidence
    )

    assert result.changed_lines == ("S3",)
    assert result.resolved == evidence.discrepancies and result.raised == ()
    assert result.evidence.discrepancies == ()
    assert set(result.refit) == {"narration:closeup", "pace"}
    beat = result.theme.narration.beat("closeup")
    assert (beat.example, beat.chars) == ("가격은 사천오백 원.", 8)
    # 다른 자리의 내레이션 규칙, 변수, 문구 규칙은 손대지 않는다
    for slot in ("greeting", "show", "outro"):
        assert result.theme.narration.beat(slot) is theme.narration.beat(slot)
    assert result.theme.variables == theme.variables
    assert result.theme.text_rules == theme.text_rules
    result.theme.validate()


def test_a_value_seen_on_screen_is_never_rewritten_from_speech():
    """화면에서 본 값은 그대로 둔다. 고친 말이 화면과 다르면 어긋남이 새로 생길 뿐이다."""
    script = _script()
    theme, evidence = _extracted(cafe_theme(), script)

    result = refit_after_correction(
        theme, script, _fix(script, "S3", "가격은 오천 원."), SCREEN, evidence
    )

    assert result.theme.variables == theme.variables
    assert [d.kind for d in result.raised] == [DiscrepancyKind.VALUE] and result.resolved == ()
    price = next(v for v in result.evidence.variables if v.key == "price")
    assert price.agreement is Agreement.CONFLICT


def test_a_value_that_came_only_from_speech_follows_the_corrected_line():
    base = cafe_theme()
    theme = replace(
        base,
        variables=(
            ThemeVariable("menu", "소개하는 메뉴 이름", "바닐라 라테"),  # 잘못 들은 이름
            ThemeVariable("size", "용량", "500ml"),
            *base.variables[2:],
        ),
        text_rules=tuple(r for r in base.text_rules if r.rule_key != "price"),
    )
    screen = (SCREEN[0], SCREEN[2], SCREEN[3])  # 메뉴 이름과 용량은 화면에 없다
    heard = _script(menu_line="바닐라 라테입니다.", price_line="오백 밀리리터 한 병입니다.")
    theme, evidence = _extracted(theme, heard, screen)
    assert {v.key: v.agreement for v in evidence.variables} == {
        "menu": Agreement.SPEECH_ONLY,
        "size": Agreement.SPEECH_ONLY,
    }
    corrected = _fix(_fix(heard, "S2", "바닐라 라떼입니다."), "S3", "삼백 밀리리터 한 병입니다.")

    result = refit_after_correction(theme, heard, corrected, screen, evidence)

    values = {v.key: v.example for v in result.theme.variables}
    assert values["menu"] == "바닐라 라떼"  # 고친 자국을 따라 같은 자리의 글을 읽는다
    assert values["size"] == "300ml"  # 수량은 옛 값이 적힌 모양으로
    assert {"variable:menu", "variable:size"} <= set(result.refit)
    assert {v.key: v.agreement for v in result.evidence.variables} == {
        "menu": Agreement.SPEECH_ONLY,
        "size": Agreement.SPEECH_ONLY,
    }


def test_a_caption_rule_keeps_its_example_in_step_with_the_corrected_line():
    base = cafe_theme()
    rules = tuple(
        replace(r, example="매장에서 만나요!") if r.rule_key == "caption" else r
        for r in base.text_rules
    )
    script = _script()
    theme, evidence = _extracted(replace(base, text_rules=rules), script)
    assert next(r for r in theme.text_rules if r.rule_key == "caption").follows_speech

    result = refit_after_correction(
        theme, script, _fix(script, "S4", "매장에서 또 만나요!"), SCREEN, evidence
    )

    caption = next(r for r in result.theme.text_rules if r.rule_key == "caption")
    assert caption.example == "매장에서 또 만나요!" and caption.follows_speech
    assert "rule:caption" in result.refit and "narration:outro" in result.refit


def test_marking_a_line_as_dialogue_takes_it_out_of_the_narration_rules():
    script = _script()
    theme, evidence = _extracted(cafe_theme(), script)
    on_screen = script.with_line(script.line("S2").with_kind(LineKind.DIALOGUE))

    result = refit_after_correction(theme, script, on_screen, SCREEN, evidence)

    assert result.changed_lines == ("S2",) and "narration:show" in result.refit
    assert result.theme.narration.beat("show") is None
    assert result.theme.narration.beat("greeting") is theme.narration.beat("greeting")


def test_nothing_changes_when_no_line_changed():
    script = _script()
    theme, evidence = _extracted(cafe_theme(), script)

    result = refit_after_correction(theme, script, script, SCREEN, evidence)

    assert result.theme is theme and result.evidence is evidence
    assert (result.changed_lines, result.refit, result.resolved, result.raised) == ((), (), (), ())
