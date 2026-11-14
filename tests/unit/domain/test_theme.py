"""테마 표현의 규칙·IR 과의 관계·직렬화 (ADR-0025)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from frame.domain.enums import EntityKind
from frame.domain.errors import NotFound, ValidationError
from frame.domain.ir import EntitySpec, RequirementSpec
from frame.domain.screen import Region, TextRole
from frame.domain.theme import (
    AnchorOrigin,
    CastMember,
    Condition,
    FormatSpec,
    NarrationBeat,
    SceneSlot,
    SourceOrigin,
    TextRule,
    TextStyle,
    Theme,
    ThemeVariable,
    render_template,
    slot_spans_from_shots,
    template_variables,
)
from frame.domain.video_analysis import shots_from_cuts
from tests.support.themes import cafe_theme


@pytest.fixture
def theme() -> Theme:
    return cafe_theme()


def _with_cast(theme: Theme, entity_key: str, **changes) -> Theme:
    return replace(
        theme,
        cast=tuple(replace(c, **changes) if c.entity_key == entity_key else c for c in theme.cast),
    )


def test_cafe_theme_is_valid(theme):
    theme.validate()
    assert theme.format.total_duration_ms == 16000
    assert theme.format.span("closeup") == (8000, 12000)
    assert theme.format.mean_shot_ms == 4000 and theme.format.cuts_per_10s == pytest.approx(1.875)


def test_conditions_use_the_ir_vocabulary():
    """술어 별칭과 값은 IR 의 요구조건과 같은 규칙으로 정규화된다 (색상 -> color, 값은 소문자)."""
    assert Condition("색상", "Red") == Condition("color", "red")
    assert Condition("조명", "Warm  cafe_lighting").value == "warm cafe lighting"
    assert Condition("text", " FRAME ").value == "FRAME"  # 문구는 철자 그대로
    with pytest.raises(ValidationError):
        Condition("color", "  ")


# ---- IR 과의 관계 ----
def test_cast_member_is_an_ir_entity(theme):
    assert theme.cast_member("barista").entity_spec() == EntitySpec(
        "barista", EntityKind.PERSON, "바리스타"
    )
    with pytest.raises(NotFound):
        theme.cast_member("ghost")


def test_fixed_requirements_carry_cast_attributes_and_scene_conditions(theme):
    requirements = theme.fixed_requirements("show", ("barista", "mug", "pastry"))

    assert requirements == (
        RequirementSpec("show", "outfit", "white shirt and brown apron", "barista"),
        RequirementSpec("show", "material", "ceramic", "mug"),
        RequirementSpec("show", "lighting", "warm cafe lighting"),
        RequirementSpec("show", "location", "cafe counter"),
    )


def test_slot_conditions_override_style_conditions(theme):
    slots = tuple(
        replace(s, conditions=(Condition("location", "cafe terrace"), Condition("camera", "wide")))
        if s.slot_key == "outro"
        else s
        for s in theme.format.slots
    )
    changed = replace(theme, format=replace(theme.format, slots=slots))
    changed.validate()

    assert [(c.predicate, c.value) for c in changed.scene_conditions("outro")] == [
        ("lighting", "warm cafe lighting"),
        ("location", "cafe terrace"),
        ("camera", "wide"),
    ]
    # 다른 자리는 스타일의 조건 그대로 + 그 자리의 것
    assert [c.predicate for c in changed.scene_conditions("closeup")] == [
        "lighting",
        "location",
        "camera",
    ]


# ---- 규칙 ----
def test_fixed_attributes_cannot_be_relations_or_per_scene_predicates(theme):
    for bad in (Condition("holds", "mug"), Condition("action", "waves hand")):
        with pytest.raises(ValidationError):
            _with_cast(theme, "barista", fixed_attributes=(bad,)).validate()
    duplicated = (Condition("outfit", "white shirt"), Condition("의상", "apron"))
    with pytest.raises(ValidationError, match="중복"):
        _with_cast(theme, "barista", fixed_attributes=duplicated).validate()


def test_keys_must_be_unique_and_slots_must_reference_known_cast(theme):
    with pytest.raises(ValidationError):
        replace(theme, cast=theme.cast + (CastMember("mug"),)).validate()
    extra = SceneSlot("greeting", 4000)
    with pytest.raises(ValidationError):
        replace(theme, format=replace(theme.format, slots=theme.format.slots + (extra,))).validate()
    ghost = SceneSlot("bonus", 4000, cast_keys=("ghost",))
    with pytest.raises(ValidationError, match="ghost"):
        replace(theme, format=replace(theme.format, slots=theme.format.slots + (ghost,))).validate()
    with pytest.raises(ValidationError):
        replace(theme, format=FormatSpec(slots=())).validate()
    with pytest.raises(ValidationError):
        replace(theme, format=FormatSpec(slots=(SceneSlot("only", 0),))).validate()


def test_text_rules_must_fit_their_slots_and_declare_variables(theme):
    def with_rule(rule: TextRule) -> Theme:
        return replace(theme, text_rules=(rule,))

    with_rule(TextRule("ok", "{menu}", TextRole.LABEL, slot_keys=("show",))).validate()
    with pytest.raises(ValidationError, match="없는 자리"):
        with_rule(TextRule("bad", "문구", slot_keys=("nowhere",))).validate()
    with pytest.raises(ValidationError, match="discount"):
        with_rule(TextRule("bad", "{menu} {discount}")).validate()
    with pytest.raises(ValidationError, match="시간"):
        with_rule(
            TextRule("bad", "문구", slot_keys=("show",), start_ms=3000, end_ms=5000)
        ).validate()
    with pytest.raises(ValidationError, match="시간"):
        with_rule(TextRule("bad", "문구", start_ms=2000, end_ms=1000)).validate()
    with pytest.raises(ValidationError):
        with_rule(TextRule("bad", "  ")).validate()
    with pytest.raises(ValidationError):
        replace(theme, text_rules=theme.text_rules + (TextRule("title", "또"),)).validate()


def test_variables_and_narration_must_be_consistent(theme):
    with pytest.raises(ValidationError):
        replace(theme, variables=theme.variables + (ThemeVariable("menu"),)).validate()
    with pytest.raises(ValidationError):
        replace(theme, variables=(ThemeVariable("메뉴"),), text_rules=()).validate()
    beats = theme.narration.beats + (NarrationBeat("nowhere"),)
    with pytest.raises(ValidationError):
        replace(theme, narration=replace(theme.narration, beats=beats)).validate()
    with pytest.raises(ValidationError):
        replace(theme, narration=replace(theme.narration, pace_cps=0.0)).validate()


def test_text_style_limits():
    with pytest.raises(ValidationError):
        TextStyle(size=0.0)
    with pytest.raises(ValidationError):
        TextStyle(background_opacity=1.5)
    assert TextStyle.from_dict(None) == TextStyle()
    assert TextStyle.from_dict({"color": "#ff0000", "background": None}).background is None


# ---- 기준 이미지와 권리 ----
def test_reference_from_the_source_is_allowed_only_for_frame_generated_videos(theme):
    anchored = theme.with_reference(
        "barista", "mem://references/barista.jpg", AnchorOrigin.SOURCE, "young woman, dark hair"
    )

    assert [c.entity_key for c in anchored.references()] == ["barista"]
    assert anchored.cast_member("barista").reference_description == "young woman, dark hair"
    assert theme.references() == ()  # 원본 테마는 그대로다

    external = cafe_theme(origin=SourceOrigin.EXTERNAL)
    with pytest.raises(ValidationError, match="외부 영상"):
        external.with_reference("barista", "mem://x.jpg", AnchorOrigin.SOURCE)
    with pytest.raises(ValidationError, match="외부 영상"):
        external.with_reference("mug", "mem://x.jpg", AnchorOrigin.SOURCE)  # 소품도 마찬가지
    # 처음 만든 편이나 사용자가 준 이미지는 된다
    external.with_reference("barista", "mem://pilot.jpg", AnchorOrigin.PILOT)
    external.with_reference("mug", "mem://user.jpg", AnchorOrigin.USER)


def test_reference_image_and_its_origin_come_together(theme):
    with pytest.raises(ValidationError, match="함께"):
        _with_cast(theme, "mug", reference_image_uri="mem://mug.jpg").validate()
    with pytest.raises(NotFound):
        theme.with_reference("ghost", "mem://x.jpg", AnchorOrigin.USER)


# ---- 문구 틀 ----
def test_template_variables_and_rendering():
    assert template_variables("{menu} {price} ({menu})") == ("menu", "price")
    assert template_variables("오늘의 메뉴") == ()
    assert render_template("{menu} {price}", {"menu": "말차 라떼", "price": "5,000원"}) == (
        "말차 라떼 5,000원"
    )
    with pytest.raises(ValidationError) as caught:
        render_template("{menu} {price}", {"menu": "말차 라떼", "price": ""})
    assert caught.value.context["variables"] == ("price",)


# ---- 형식: 컷을 자리로 묶기 ----
def test_slots_follow_the_shots_when_they_are_long_enough():
    shots = shots_from_cuts([4000, 8000, 12000], 16000)

    assert slot_spans_from_shots(shots) == ((0, 4000), (4000, 8000), (8000, 12000), (12000, 16000))


def test_short_shots_are_merged_until_a_slot_is_long_enough():
    """1초 컷이 이어지는 영상: 3초 이상이 될 때까지 묶고, 끝에 남은 짧은 컷은 앞 자리에 붙인다."""
    shots = shots_from_cuts([1000, 2000, 3000, 4500, 9000, 10000], 11000)

    assert slot_spans_from_shots(shots, min_slot_ms=3000) == ((0, 3000), (3000, 11000))
    assert slot_spans_from_shots(shots_from_cuts([], 2000)) == ((0, 2000),)


def test_slot_boundaries_snap_to_the_grid_and_stay_contiguous():
    shots = shots_from_cuts([4033, 8066], 12033)
    spans = slot_spans_from_shots(shots)

    assert spans == ((0, 4000), (4000, 8100), (8100, 12000))
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:], strict=False))
    with pytest.raises(ValidationError):
        slot_spans_from_shots(())


# ---- 직렬화 ----
def test_theme_round_trips_as_json(theme):
    anchored = theme.with_reference(
        "mug", "mem://mug.jpg", AnchorOrigin.SOURCE, "white ceramic mug"
    )
    restored = Theme.from_json(anchored.to_json())

    assert restored == anchored
    restored.validate()
    assert restored.text_rules[1].region == Region(0.1, 0.72, 0.8, 0.08)
    assert restored.text_rules[1].style.color == "#ffe08a"
    assert Theme.from_dict({**theme.to_dict(), "summary": "다른 요약"}).summary == "다른 요약"
    with pytest.raises(ValidationError, match="schema_version"):
        Theme.from_dict({**theme.to_dict(), "schema_version": 99})


def test_fingerprint_ignores_the_id_but_not_the_content(theme):
    assert replace(theme, theme_id="thm_other").fingerprint() == theme.fingerprint()
    assert replace(theme, title="다른 테마").fingerprint() != theme.fingerprint()


def test_colour_notation_is_normalised_to_css_names_or_hex():
    from frame.domain.theme import color_name

    assert color_name("white") == "white" and color_name("#FFE08A") == "#ffe08a"
    assert color_name("Light Yellow") == "lightyellow"
    # gray·grey 는 이름마다 받는 철자가 다르다
    assert color_name("light gray") == "lightgrey" and color_name("dark grey") == "darkgray"
    assert color_name("dark_slate_gray") == "darkslategray"
    for unknown in ("shiny rainbow", "rgba(0,0,0,0.5)", "#fff", "", None, "white:enable=0"):
        assert color_name(unknown) is None
