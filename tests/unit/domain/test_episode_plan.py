"""편 계획: 테마의 고정 요소 + 이번 편의 내용 -> IR·녹음 대본·화면 문구 (ADR-0026)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from frame.domain.enums import EntityKind
from frame.domain.errors import NotFound, ValidationError
from frame.domain.ir import EntitySpec, RequirementSpec
from frame.domain.screen import Region, TextRole
from frame.domain.series.episode import (
    DEFAULT_REGIONS,
    EpisodeBrief,
    EpisodeContent,
    EpisodePlan,
    OverlayItem,
    OverlayKind,
    PlanPolicy,
    SlotContent,
    build_episode_plan,
)
from frame.domain.series.theme import TextRule, TextStyle
from tests.support.themes import cafe_theme, matcha_brief, matcha_content


@pytest.fixture
def theme():
    return cafe_theme()


@pytest.fixture
def plan(theme) -> EpisodePlan:
    return build_episode_plan(theme, matcha_brief(), matcha_content())


def _slot(content: EpisodeContent, slot_key: str, **changes) -> EpisodeContent:
    return replace(
        content,
        slots=tuple(replace(s, **changes) if s.slot_key == slot_key else s for s in content.slots),
    )


# ---- IR: 형식과 고정 요소는 테마에서 온다 ----
def test_scenes_follow_the_theme_slots(theme, plan):
    assert [s.scene_key for s in plan.ir.scenes] == ["greeting", "show", "closeup", "outro"]
    assert [(s.planned_start_ms, s.planned_end_ms) for s in plan.ir.scenes] == [
        (0, 4000),
        (4000, 8000),
        (8000, 12000),
        (12000, 16000),
    ]
    assert plan.ir.scene("show").description == "바리스타가 말차 라떼가 담긴 컵을 들어 보인다"
    assert plan.ir.scene("outro").description == "바리스타가 손을 흔들며 마무리한다"  # 자리의 구실
    assert plan.title == "말차 라떼 편" and plan.seed == 7


def test_cast_and_episode_entities_are_merged(plan):
    assert [(e.entity_key, e.kind) for e in plan.ir.entities] == [
        ("barista", EntityKind.PERSON),
        ("mug", EntityKind.PROP),
        ("latte", EntityKind.PROP),
    ]
    assert plan.ir.scene("show").entity_keys == ("barista", "mug", "latte")
    assert plan.ir.scene("greeting").entity_keys == ("barista",)


def test_fixed_attributes_and_style_are_requirements_of_every_scene(plan):
    """고정 출연이 나오는 모든 장면에 고정 속성이, 모든 장면에 스타일 조건이 걸린다."""
    for scene in plan.ir.scenes:
        keys = {r.key: r.expected_value for r in scene.requirements}
        assert keys[f"{scene.scene_key}:-:lighting"] == "warm cafe lighting"
        if "barista" in scene.entity_keys:
            assert keys[f"{scene.scene_key}:barista:outfit"] == "white shirt and brown apron"
        if "mug" in scene.entity_keys:
            assert keys[f"{scene.scene_key}:mug:material"] == "ceramic"
    closeup = {r.key: r.expected_value for r in plan.ir.scene("closeup").requirements}
    assert closeup["closeup:-:camera"] == "close up"  # 자리의 조건
    assert closeup["closeup:latte:color"] == "green"  # 이 편만의 조건
    show = {r.key for r in plan.ir.scene("show").requirements}
    assert "show:barista:holds:mug" in show


def test_content_cannot_override_what_the_theme_fixes(theme):
    content = _slot(
        matcha_content(),
        "greeting",
        requirements=(
            RequirementSpec("greeting", "outfit", "black hoodie", "barista"),
            RequirementSpec(
                "greeting", "lighting", "warm cafe lighting"
            ),  # 같은 값은 조용히 넘어간다
            RequirementSpec("greeting", "expression", "smiling", "barista"),
        ),
    )
    plan = build_episode_plan(theme, matcha_brief(), content)

    values = {r.key: r.expected_value for r in plan.ir.scene("greeting").requirements}
    assert values["greeting:barista:outfit"] == "white shirt and brown apron"
    assert values["greeting:barista:expression"] == "smiling"
    assert len(values) == len(plan.ir.scene("greeting").requirements)  # 중복 없이
    assert any("black hoodie" in note and "고정값" in note for note in plan.notes)
    assert not any("lighting" in note for note in plan.notes)


def test_content_must_fill_exactly_the_theme_slots(theme):
    content = matcha_content()
    with pytest.raises(ValidationError) as caught:
        build_episode_plan(theme, matcha_brief(), replace(content, slots=content.slots[:3]))
    assert caught.value.context["missing"] == ("outro",)
    extra = content.slots + (SlotContent("bonus", "덤"),)
    with pytest.raises(ValidationError) as caught:
        build_episode_plan(theme, matcha_brief(), replace(content, slots=extra))
    assert caught.value.context["unknown"] == ("bonus",)
    twice = content.slots + (content.slots[0],)
    with pytest.raises(ValidationError, match="두 번"):
        build_episode_plan(theme, matcha_brief(), replace(content, slots=twice))


def test_entities_must_be_defined_and_cannot_shadow_the_cast(theme):
    with pytest.raises(ValidationError, match="ghost"):
        build_episode_plan(
            theme, matcha_brief(), _slot(matcha_content(), "outro", entity_keys=("ghost",))
        )
    shadow = replace(matcha_content(), entities=(EntitySpec("mug", EntityKind.PROP, "다른 컵"),))
    with pytest.raises(ValidationError, match="고정 출연"):
        build_episode_plan(theme, matcha_brief(), shadow)
    with pytest.raises(ValidationError, match="다른 장면"):
        build_episode_plan(
            theme,
            matcha_brief(),
            _slot(
                matcha_content(),
                "outro",
                requirements=(RequirementSpec("show", "mood", "calm"),),
            ),
        )


def test_another_cast_member_can_join_a_slot_and_unused_entities_are_dropped(theme):
    content = replace(
        _slot(matcha_content(), "outro", entity_keys=("mug",)),
        entities=matcha_content().entities + (EntitySpec("straw", EntityKind.PROP, "빨대"),),
    )
    plan = build_episode_plan(theme, matcha_brief(), content)

    assert plan.ir.scene("outro").entity_keys == ("barista", "mug")
    assert "outro:mug:material" in {r.key for r in plan.ir.scene("outro").requirements}
    assert not plan.ir.has_entity("straw")
    assert any("straw" in note for note in plan.notes)


# ---- 변수 ----
def test_brief_values_win_over_planned_values(theme):
    content = replace(matcha_content(), variables={"menu": "녹차 라떼", "price": "5,000원"})
    plan = build_episode_plan(theme, matcha_brief(), content)

    assert plan.variables == {"menu": "말차 라떼", "price": "5,000원"}
    assert any("녹차 라떼" in note and "말차 라떼" in note for note in plan.notes)


def test_planner_may_fill_variables_the_brief_left_out(theme):
    brief = matcha_brief(variables={"menu": "말차 라떼"})
    content = replace(matcha_content(), variables={"price": "5,000원", "unknown": "x"})

    assert build_episode_plan(theme, brief, content).variables["price"] == "5,000원"
    with pytest.raises(ValidationError) as caught:
        build_episode_plan(theme, brief, matcha_content())
    assert caught.value.context["variables"] == ("price",)


def test_unknown_brief_variable_is_rejected(theme):
    with pytest.raises(ValidationError) as caught:
        build_episode_plan(
            theme,
            matcha_brief(variables={"menu": "말차 라떼", "prise": "5,000원"}),
            matcha_content(),
        )
    assert caught.value.context["variables"] == ("prise",)
    with pytest.raises(ValidationError, match="제목"):
        build_episode_plan(theme, matcha_brief(title="  "), matcha_content())


# ---- 화면 문구 ----
def test_overlays_come_from_text_rules_and_variables(theme, plan):
    by_id = {o.overlay_id: o for o in plan.overlays}

    assert set(by_id) == {"greeting:title", "closeup:price", "greeting:caption"}
    price = by_id["closeup:price"]
    assert price.text == "말차 라떼 5,000원" and price.role is TextRole.PRICE
    assert (price.start_ms, price.end_ms) == (500, 3500)
    assert price.region == Region(0.1, 0.72, 0.8, 0.08) and price.style.color == "#ffe08a"
    assert price.rule_key == "price" and price.kind is OverlayKind.TEXT
    title = by_id["greeting:title"]
    assert title.text == "오늘의 메뉴" and (title.start_ms, title.end_ms) == (0, 4000)
    # 자리별 자막은 값이 있는 자리에만 들어간다. 자리가 없는 규칙은 구실에 따른 기본 자리를 쓴다
    caption = by_id["greeting:caption"]
    assert (
        caption.text == "오늘은 초록초록!" and caption.region == DEFAULT_REGIONS[TextRole.CAPTION]
    )
    assert plan.overlays_for("closeup") == (price,)


def test_overlay_text_is_not_a_generation_condition(theme):
    """문구만 다른 두 편은 장면 조건(fingerprint)이 같다: 문구를 고쳐도 영상은 다시 만들지 않는다."""
    first = build_episode_plan(theme, matcha_brief(), matcha_content())
    second = build_episode_plan(
        theme, matcha_brief(variables={"menu": "말차 라떼", "price": "5,500원"}), matcha_content()
    )

    assert first.overlay("closeup:price").text != second.overlay("closeup:price").text
    assert first.ir.fingerprint() == second.ir.fingerprint()
    assert not any(e.kind is EntityKind.TEXT for e in first.ir.entities)


def test_rule_without_slots_applies_to_every_slot(theme):
    rules = (TextRule("brand", "FRAME CAFE", TextRole.WATERMARK),)
    plan = build_episode_plan(replace(theme, text_rules=rules), matcha_brief(), matcha_content())

    assert [o.overlay_id for o in plan.overlays] == [
        "greeting:brand",
        "show:brand",
        "closeup:brand",
        "outro:brand",
    ]


# ---- 녹음 대본 ----
def test_script_has_one_line_per_spoken_slot_with_a_time_budget(theme, plan):
    assert [(line.line_key, line.scene_key) for line in plan.script] == [
        ("L1", "greeting"),
        ("L2", "show"),
        ("L3", "closeup"),
        ("L4", "outro"),
    ]
    first = plan.line("L1")
    assert first.text == "안녕하세요, 오늘의 메뉴예요."
    assert first.budget_ms == 3600  # 4초 장면에서 앞뒤 0.2초씩 뺀 시간
    assert first.estimated_ms == round(12 / 5.5 * 1000) and first.fits is True


def test_long_line_is_flagged_and_silent_slots_have_no_line(theme):
    long_text = "안녕하세요 여러분 오늘은 정말 특별한 메뉴를 준비했는데요 바로 말차 라떼입니다"
    content = _slot(
        _slot(matcha_content(), "greeting", narration=long_text), "show", narration="  "
    )
    plan = build_episode_plan(theme, matcha_brief(), content)

    assert [line.scene_key for line in plan.script] == ["greeting", "closeup", "outro"]
    assert plan.line("L1").fits is False
    assert any("L1" in note and "깁니다" in note for note in plan.notes)


def test_pace_falls_back_to_the_policy_when_the_theme_has_none(theme):
    quiet = replace(theme, narration=replace(theme.narration, pace_cps=None))
    plan = build_episode_plan(
        quiet,
        matcha_brief(),
        matcha_content(),
        PlanPolicy(lead_in_ms=0, tail_ms=0, default_pace_cps=4.0),
    )

    assert plan.line("L1").budget_ms == 4000
    assert plan.line("L1").estimated_ms == 3000  # 12글자 ÷ 4


def test_without_any_pace_the_script_length_is_not_estimated(theme):
    """말 빠르기를 테마도 정책도 모르면 대본의 길이를 어림하지 않는다. 넘친다는 경고도 내지 않는다."""
    quiet = replace(theme, narration=replace(theme.narration, pace_cps=None))

    plan = build_episode_plan(
        quiet, matcha_brief(), matcha_content(), PlanPolicy(default_pace_cps=0.0)
    )

    assert plan.script and all(line.estimated_ms is None for line in plan.script)
    assert all(line.fits is None for line in plan.script)
    assert not any("깁니다" in note for note in plan.notes)


# ---- 계획 고치기와 직렬화 ----
def test_overlay_can_be_replaced_added_and_removed(plan):
    fixed = replace(plan.overlay("closeup:price"), text="말차 라떼 5,500원")
    changed = plan.with_overlay(fixed)

    assert changed.overlay("closeup:price").text == "말차 라떼 5,500원"
    assert plan.overlay("closeup:price").text == "말차 라떼 5,000원"  # 원래 계획은 그대로
    assert len(changed.overlays) == len(plan.overlays)
    patch = OverlayItem(
        "show:patch1",
        "show",
        "FRAME",
        1000,
        3000,
        Region(0.4, 0.5, 0.2, 0.05),
        TextStyle(color="black", background="white", background_opacity=1.0),
        kind=OverlayKind.PATCH,
    )
    assert len(changed.with_overlay(patch).overlays) == len(plan.overlays) + 1
    assert len(changed.without_overlay("greeting:title").overlays) == len(plan.overlays) - 1
    with pytest.raises(NotFound):
        plan.without_overlay("nope")
    with pytest.raises(ValidationError, match="시간"):
        plan.with_overlay(replace(patch, end_ms=9000))
    with pytest.raises(NotFound):
        plan.with_overlay(replace(patch, scene_key="nowhere"))


def test_plan_round_trips(plan):
    restored = EpisodePlan.from_dict(plan.to_dict())

    assert restored == plan
    restored.validate()
    assert EpisodeBrief.from_dict(matcha_brief().to_dict()) == matcha_brief()


# ---- 편 안에서 객체의 모습은 장면마다 같다 ----
def test_an_objects_look_is_the_same_in_every_scene_it_appears_in(theme):
    """기획이 한 장면에만 적은 모습(색·재질·글자)을 그 객체가 나오는 나머지 장면에도 건다.

    둘째 실호출: 컵의 글자가 클로즈업 장면에만 걸려, 손에 든 장면의 컵에는 글자가 없이 생성됐다.
    """
    content = _slot(
        matcha_content(),
        "closeup",
        requirements=(
            RequirementSpec("closeup", "color", "green", "latte"),
            RequirementSpec("closeup", "text", "MATCHA", "latte"),
            RequirementSpec("closeup", "position", "on the counter", "latte"),  # 장면마다 다른 것
        ),
    )
    plan = build_episode_plan(theme, matcha_brief(), content)

    show = {r.key: r.expected_value for r in plan.ir.scene("show").requirements}
    assert show["show:latte:text"] == "MATCHA" and show["show:latte:color"] == "green"
    assert "show:latte:position" not in show  # 위치·행동·표정은 옮기지 않는다
    assert any("latte" in n and "text" in n and "show" in n for n in plan.notes)
    # 그 객체가 나오지 않는 장면에는 걸지 않는다
    assert not any(r.entity_key == "latte" for r in plan.ir.scene("greeting").requirements)


def test_first_stated_look_wins_when_scenes_disagree(theme):
    content = _slot(
        matcha_content(),
        "closeup",
        requirements=(RequirementSpec("closeup", "color", "pink", "latte"),),
    )
    plan = build_episode_plan(theme, matcha_brief(), content)

    closeup = {r.key: r.expected_value for r in plan.ir.scene("closeup").requirements}
    assert closeup["closeup:latte:color"] == "green"  # 먼저 나온 장면(show)의 값
    assert any("pink" in n and "green" in n and "closeup" in n for n in plan.notes)
    assert len(closeup) == len(plan.ir.scene("closeup").requirements)


def test_relations_and_scene_conditions_are_not_spread(theme):
    plan = build_episode_plan(theme, matcha_brief(), matcha_content())

    closeup = {r.key for r in plan.ir.scene("closeup").requirements}
    assert "closeup:barista:holds:mug" not in closeup  # 관계는 그 장면의 것
    greeting = {r.key for r in plan.ir.scene("greeting").requirements}
    assert "greeting:-:camera" not in greeting  # 자리의 조건(클로즈업)은 다른 자리로 가지 않는다
