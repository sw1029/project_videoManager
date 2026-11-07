"""US-01 AC-02: 선택 속성 변경이 연관 장면에 자동 전파된다 (원본 IR 은 불변)."""

import pytest

from frame.domain.enums import ChangeScope
from frame.domain.errors import NotFound, ValidationError
from frame.domain.services.propagation import AttributeChange, DefaultPropagationPolicy

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
