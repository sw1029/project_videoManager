"""US-01 AC-02: 선택 속성 변경이 연관 장면에 자동 전파된다 (원본 IR 은 불변)."""

from dataclasses import replace

import pytest

from frame.domain.enums import ChangeScope, EntityKind
from frame.domain.errors import NotFound, ValidationError
from frame.domain.ir import EntitySpec, IntermediateRepresentation, RequirementSpec, SceneSpec
from frame.domain.revision import AttributeChange, DefaultPropagationPolicy

policy = DefaultPropagationPolicy()


def _value(ir, scene_key, entity_key, predicate):
    return next(
        r.expected_value
        for r in ir.scene(scene_key).requirements
        if r.entity_key == entity_key and r.predicate == predicate
    )


@pytest.mark.ac("AC-02")
def test_change_propagates_to_all_scenes_where_entity_appears(ir):
    result = policy.apply(ir, AttributeChange("cup", "color", "teal"))

    assert result.changed_scene_keys == ("intro", "closeup")
    assert _value(result.ir, "intro", "cup", "color") == "teal"
    assert _value(result.ir, "closeup", "cup", "color") == "teal"
    assert result.ir.scene("outro") == ir.scene("outro")  # 컵이 없는 장면은 그대로


def test_source_ir_is_not_mutated(ir):
    before = ir.to_dict()
    policy.apply(ir, AttributeChange("cup", "color", "teal"))
    assert ir.to_dict() == before


def test_scope_scenes_limits_change_to_given_scenes(ir):
    change = AttributeChange("cup", "color", "teal", ChangeScope.SCENES, ("closeup",))
    result = policy.apply(ir, change)

    assert result.changed_scene_keys == ("closeup",)
    assert _value(result.ir, "intro", "cup", "color") == "red"
    assert _value(result.ir, "closeup", "cup", "color") == "teal"


def test_new_predicate_is_added_when_missing(ir):
    result = policy.apply(ir, AttributeChange("cup", "material", "glass"))
    assert result.changed_scene_keys == ("intro", "closeup")
    assert _value(result.ir, "closeup", "cup", "material") == "glass"
    result.ir.validate()


def test_same_value_yields_no_change(ir):
    result = policy.apply(ir, AttributeChange("cup", "color", "red"))
    assert result.changed_scene_keys == ()
    assert result.ir == ir


def test_unknown_entity_is_not_found(ir):
    with pytest.raises(NotFound):
        policy.apply(ir, AttributeChange("ghost", "color", "teal"))


def test_scope_scenes_requires_scene_keys(ir):
    with pytest.raises(ValidationError):
        policy.apply(ir, AttributeChange("cup", "color", "teal", ChangeScope.SCENES, ()))


def test_scope_scenes_rejects_scene_without_the_entity(ir):
    with pytest.raises(ValidationError, match="outro"):
        policy.apply(ir, AttributeChange("cup", "color", "teal", ChangeScope.SCENES, ("outro",)))


def test_scope_scenes_rejects_unknown_scene(ir):
    with pytest.raises(NotFound):
        policy.apply(ir, AttributeChange("cup", "color", "teal", ChangeScope.SCENES, ("ghost",)))


# ---- 관계 조건 수정 (2차 검토) ---------------------------------------------------------


def test_relation_change_updates_related_entity_and_value(ir):
    intro = ir.scene("intro")
    with_phone = replace(
        ir,
        entities=ir.entities + (EntitySpec("phone", EntityKind.PROP),),
        scenes=tuple(
            replace(s, entity_keys=s.entity_keys + ("phone",)) if s.scene_key == "intro" else s
            for s in ir.scenes
        ),
    )
    result = policy.apply(
        with_phone, AttributeChange("barista", "holds", "phone", ChangeScope.SCENES, ("intro",))
    )
    holds = next(r for r in result.ir.scene("intro").requirements if r.predicate == "holds")
    assert (holds.expected_value, holds.related_entity_key) == ("phone", "phone")
    assert holds.key == "intro:barista:holds:phone"
    assert len(intro.requirements) == len(result.ir.scene("intro").requirements)
    result.ir.validate()


def test_relation_change_to_background_is_rejected(ir):
    with pytest.raises(ValidationError, match="배경"):
        policy.apply(
            ir, AttributeChange("barista", "holds", "cafe", ChangeScope.SCENES, ("intro",))
        )


def test_relation_change_to_entity_outside_scene_is_rejected(ir):
    # outro 에는 cup 이 없다
    with pytest.raises(ValidationError, match="연결"):
        policy.apply(ir, AttributeChange("barista", "holds", "cup", ChangeScope.SCENES, ("outro",)))


def test_ambiguous_relation_requires_current_value():
    two = IntermediateRepresentation(
        entities=(EntitySpec("barista"), EntitySpec("cup"), EntitySpec("phone"), EntitySpec("bag")),
        scenes=(
            SceneSpec(
                "intro",
                0,
                1000,
                "",
                ("barista", "cup", "phone", "bag"),
                (
                    RequirementSpec("intro", "holds", "cup", "barista", "cup"),
                    RequirementSpec("intro", "holds", "phone", "barista", "phone"),
                ),
            ),
        ),
    )
    with pytest.raises(ValidationError, match="current_value"):
        policy.apply(two, AttributeChange("barista", "holds", "bag"))
    result = policy.apply(two, AttributeChange("barista", "holds", "bag", current_value="phone"))
    assert sorted(r.related_entity_key for r in result.ir.scene("intro").requirements) == [
        "bag",
        "cup",
    ]


def test_current_value_without_match_changes_nothing(ir):
    result = policy.apply(ir, AttributeChange("cup", "color", "teal", current_value="blue"))
    assert result.changed_scene_keys == ()


def test_predicate_alias_in_change_is_normalized(ir):
    result = policy.apply(ir, AttributeChange("cup", "색상", "teal"))
    assert result.changed_scene_keys == ("intro", "closeup")
    predicates = {
        r.predicate for r in result.ir.scene("intro").requirements if r.entity_key == "cup"
    }
    assert predicates == {"color"}


def test_change_values_are_normalized(ir):
    result = policy.apply(ir, AttributeChange("cup", "color", " Teal "))
    assert {
        r.expected_value for r in result.ir.scene("intro").requirements if r.predicate == "color"
    } == {"teal"}
    # current_value 도 정규화되어 'Red' 가 저장된 'red' 와 맞는다
    matched = policy.apply(ir, AttributeChange("cup", "color", "teal", current_value="Red"))
    assert matched.changed_scene_keys == ("intro", "closeup")


# ---- 장면 수준 조건 (entity_key=None): 장소·조명·분위기 수정 --------------------------------


def _with_scene_level(ir):
    return replace(
        ir,
        scenes=tuple(
            replace(
                s, requirements=s.requirements + (RequirementSpec(s.scene_key, "location", "cafe"),)
            )
            for s in ir.scenes
        ),
    )


def test_scene_level_change_applies_to_every_scene(ir):
    result = policy.apply(_with_scene_level(ir), AttributeChange(None, "장소", "Park"))
    assert result.changed_scene_keys == ("intro", "closeup", "outro")
    for scene in result.ir.scenes:
        loc = [r for r in scene.requirements if r.entity_key is None and r.predicate == "location"]
        assert [r.expected_value for r in loc] == ["park"]
    result.ir.validate()


def test_scene_level_change_adds_condition_and_respects_scene_scope(ir):
    result = policy.apply(ir, AttributeChange(None, "mood", "calm", ChangeScope.SCENES, ("outro",)))
    assert result.changed_scene_keys == ("outro",)
    assert any(r.key == "outro:-:mood" for r in result.ir.scene("outro").requirements)
    assert all(r.predicate != "mood" for r in result.ir.scene("intro").requirements)


def test_scene_level_change_rejects_relation_predicates(ir):
    with pytest.raises(ValidationError, match="관계"):
        policy.apply(ir, AttributeChange(None, "holds", "cup"))


def test_scene_level_change_rejects_unknown_scene(ir):
    with pytest.raises(NotFound):
        policy.apply(ir, AttributeChange(None, "mood", "calm", ChangeScope.SCENES, ("ghost",)))
