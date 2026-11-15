"""대본을 테마의 근거로 쓴다 (ADR-0028): 변수 값·화면 문구를 말과 견주고, 어긋남을 남기고, 내레이션 규칙을 잰다."""

from __future__ import annotations

from dataclasses import replace

import pytest

from frame.domain.screen import TextRole
from frame.domain.script import LineKind, SceneSpan, build_script
from frame.domain.series.script_evidence import (
    Agreement,
    DiscrepancyKind,
    ScriptEvidence,
    TextRelation,
    apply_script,
    link_script,
    narration_from_script,
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


def _script(price_line: str = "가격은 사천오백 원.", last_line: str = "매장에서 만나요!", extra=()):
    segments = (
        SpeechSegment(400, 2600, "안녕하세요, 오늘의 메뉴예요."),
        SpeechSegment(4500, 6200, "바닐라 라떼입니다."),
        SpeechSegment(8600, 10400, price_line),
        SpeechSegment(12500, 14000, last_line),
        *extra,
    )
    return build_script(Transcript("ko", segments), SCENES, duration_ms=16000)


def _variable(evidence: ScriptEvidence, key: str):
    return next(v for v in evidence.variables if v.key == key)


def _text(evidence: ScriptEvidence, text: str):
    return next(t for t in evidence.texts if t.text == text)


def test_values_shown_on_screen_and_said_agree():
    evidence = link_script(cafe_theme(), _script(), SCREEN)

    menu, price = _variable(evidence, "menu"), _variable(evidence, "price")
    assert (menu.agreement, menu.line_ids) == (Agreement.AGREE, ("S2",))
    # 화면에는 '4,500원', 말로는 '사천오백 원': 값으로 견줘 같은 것으로 본다
    assert (price.agreement, price.line_ids) == (Agreement.AGREE, ("S3",))
    assert evidence.discrepancies == ()


def test_a_different_value_in_speech_is_a_discrepancy_not_a_silent_choice():
    evidence = link_script(cafe_theme(), _script(price_line="가격은 오천 원."), SCREEN)

    price = _variable(evidence, "price")
    assert (price.agreement, price.line_ids, price.said) == (Agreement.CONFLICT, ("S3",), "5000원")
    (found,) = evidence.discrepancies
    assert (found.kind, found.slot_key, found.line_ids) == (
        DiscrepancyKind.VALUE,
        "closeup",
        ("S3",),
    )
    assert found.shown == "바닐라 라떼 4,500원" and found.said == "가격은 오천 원."
    assert "4500" in found.detail and "5000" in found.detail


def test_value_only_on_screen_or_only_in_speech_is_told_apart():
    theme = cafe_theme()
    theme = replace(theme, variables=(*theme.variables, ThemeVariable("size", "용량", "500ml")))
    silent_price = _script(price_line="정말 맛있어요.")
    spoken_size = _script(price_line="가격은 사천오백 원, 오백 밀리리터 한 병.")

    assert _variable(link_script(theme, silent_price, SCREEN), "price").agreement is (
        Agreement.SCREEN_ONLY
    )
    size = _variable(link_script(theme, spoken_size, SCREEN), "size")
    assert (size.agreement, size.line_ids) == (Agreement.SPEECH_ONLY, ("S3",))
    assert _variable(link_script(theme, silent_price, SCREEN), "size").agreement is Agreement.UNSEEN
    # 자리마다 값이 다른 변수(자막)는 견줄 값이 하나가 아니다: 근거를 따지지 않는다
    assert all(v.key != "caption" for v in link_script(theme, spoken_size, SCREEN).variables)


def test_screen_text_that_writes_down_the_speech_is_a_caption():
    evidence = link_script(cafe_theme(), _script(), SCREEN)

    caption = _text(evidence, "안녕하세요, 오늘의 메뉴예요")
    assert (caption.relation, caption.line_ids, caption.slot_key) == (
        TextRelation.SPOKEN,
        ("S1",),
        "greeting",
    )
    assert caption.similarity == pytest.approx(1.0)
    # 말의 일부이거나 같은 값을 담은 문구는 자막이 아니라 '말에 나온 문구' 다
    assert _text(evidence, "오늘의 메뉴").relation is TextRelation.PARTLY
    assert _text(evidence, "바닐라 라떼 4,500원").relation is TextRelation.PARTLY
    unspoken = link_script(cafe_theme(), _script(), (ScreenText("@frame_cafe", 0, 16000),))
    assert unspoken.texts[0].relation is TextRelation.UNSPOKEN and unspoken.texts[0].line_ids == ()


def test_a_caption_that_differs_from_the_speech_is_reported():
    """자막과 말이 조금 다르다: 화면을 잘못 읽었거나 말을 잘못 받아 적었다. 어느 쪽인지 고르지 않는다."""
    evidence = link_script(cafe_theme(), _script(last_line="가게에서 만나요!"), SCREEN)

    caption = _text(evidence, "매장에서 만나요!")
    assert caption.relation is TextRelation.PARTLY and 0.6 <= caption.similarity < 0.9
    (found,) = evidence.discrepancies
    assert (found.kind, found.line_ids, found.shown, found.said) == (
        DiscrepancyKind.WORDING,
        ("S4",),
        "매장에서 만나요!",
        "가게에서 만나요!",
    )


def test_quantities_said_but_linked_to_nothing_are_listed():
    extra = (SpeechSegment(14200, 15800, "배달비는 삼천 원입니다."),)

    evidence = link_script(cafe_theme(), _script(extra=extra), SCREEN)

    assert [(u.line_id, u.text) for u in evidence.unlinked] == [("S5", "3000원")]


def test_narration_rules_are_measured_per_slot():
    theme = cafe_theme()
    script = _script()

    narration = narration_from_script(theme, script)

    assert narration.present and narration.language == "ko"
    assert narration.pace_cps == pytest.approx(script.pace_cps)
    assert narration.tone == theme.narration.tone  # 말투는 소리로 재는 것이 아니다. 그대로 둔다
    greeting = narration.beat("greeting")
    assert greeting.example == "안녕하세요, 오늘의 메뉴예요."
    assert greeting.purpose == theme.narration.beat("greeting").purpose
    assert (greeting.chars, greeting.spoken_ms, greeting.lead_in_ms, greeting.line_ids) == (
        12,
        2200,
        400,
        ("S1",),
    )
    assert [b.slot_key for b in narration.beats] == ["greeting", "show", "closeup", "outro"]


def test_dialogue_and_empty_scripts_are_not_narration_to_reproduce():
    theme = cafe_theme()
    script = _script()
    spoken_on_screen = script.with_line(script.line("S2").with_kind(LineKind.DIALOGUE))

    narration = narration_from_script(theme, spoken_on_screen)

    assert narration.beat("show") is None  # 화면 속 대사는 녹음을 붙여 다시 만들 수 없다
    silent = narration_from_script(theme, build_script(None, SCENES))
    assert not silent.present and silent.beats == () and silent.pace_cps is None


def test_lines_without_letters_are_never_narration_and_never_give_a_zero_pace():
    """사람이 줄을 '...' 로 고쳐 둔 대본이어도 말 빠르기가 0.0 이 되지 않는다 (테마의 규칙에 어긋난다)."""
    theme = cafe_theme()
    script = _script()
    blank = replace(script, lines=tuple(replace(line, text="...") for line in script.lines))

    narration = narration_from_script(theme, blank)

    assert not narration.present and narration.pace_cps is None and narration.beats == ()
    assert blank.pace_cps is None
    replace(theme, narration=narration).validate()


def test_applying_the_script_marks_caption_rules_and_measures_narration():
    theme = cafe_theme()
    script = _script()
    evidence = link_script(theme, script, SCREEN)

    applied = apply_script(theme, script, evidence)

    rules = {rule.rule_key: rule.follows_speech for rule in applied.text_rules}
    assert rules == {"title": False, "price": False, "caption": True}
    assert applied.narration == narration_from_script(theme, script)
    applied.validate()
    # 말을 받아 적은 자막이 없으면 자막 규칙이라도 말을 따른다고 하지 않는다
    no_captions = link_script(theme, script, SCREEN[:2])
    assert not any(r.follows_speech for r in apply_script(theme, script, no_captions).text_rules)


def test_evidence_round_trips():
    evidence = link_script(
        cafe_theme(),
        _script(
            price_line="가격은 오천 원.", extra=(SpeechSegment(14200, 15800, "배달비 삼천 원"),)
        ),
        SCREEN,
    )

    assert ScriptEvidence.from_dict(evidence.to_dict()) == evidence
