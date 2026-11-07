"""구조화된 중간표현(IR)의 무결성 규칙·직렬화·조건 고정(fingerprint)."""

from dataclasses import replace

import pytest

from frame.domain.enums import EntityKind
from frame.domain.errors import NotFound, ValidationError
from frame.domain.ir import EntitySpec, IntermediateRepresentation, RequirementSpec, SceneSpec


def test_valid_ir_passes_validation(ir):
    ir.validate()


def test_duplicate_entity_key_is_rejected(ir):
    bad = replace(ir, entities=ir.entities + (EntitySpec("cup", EntityKind.PROP),))
    with pytest.raises(ValidationError, match="entity_key"):
        bad.validate()


def test_duplicate_scene_key_is_rejected(ir):
    bad = replace(ir, scenes=ir.scenes + (ir.scenes[0],))
    with pytest.raises(ValidationError, match="scene_key"):
        bad.validate()


def test_scene_must_reference_defined_entities(ir):
    bad = ir.with_scene(replace(ir.scene("outro"), entity_keys=("barista", "ghost")))
    with pytest.raises(ValidationError, match="ghost"):
        bad.validate()


def test_requirement_target_must_be_linked_to_its_scene(ir):
    # closeup 장면에는 barista 가 없다 -> 규칙 4 위반
    closeup = ir.scene("closeup")
    bad = ir.with_scene(
        replace(
            closeup,
            requirements=closeup.requirements
            + (RequirementSpec("closeup", "shirt_color", "white", "barista"),),
        )
    )
    with pytest.raises(ValidationError, match="barista"):
        bad.validate()


def test_scene_time_range_must_be_increasing(ir):
    bad = ir.with_scene(replace(ir.scene("intro"), planned_end_ms=0))
    with pytest.raises(ValidationError, match="시간 구간"):
        bad.validate()


def test_empty_ir_is_rejected():
    with pytest.raises(ValidationError):
        IntermediateRepresentation().validate()


def test_dict_and_json_roundtrip(ir):
    assert IntermediateRepresentation.from_dict(ir.to_dict()) == ir
    assert IntermediateRepresentation.from_json(ir.to_json()) == ir


def test_fingerprint_is_deterministic_and_sensitive_to_content(ir):
    assert ir.fingerprint() == IntermediateRepresentation.from_dict(ir.to_dict()).fingerprint()
    changed = ir.with_scene(
        replace(
            ir.scene("closeup"), requirements=(RequirementSpec("closeup", "color", "teal", "cup"),)
        )
    )
    assert changed.fingerprint() != ir.fingerprint()


def test_scene_fingerprint_changes_only_for_the_affected_scene(ir):
    closeup = ir.scene("closeup")
    changed = ir.with_scene(
        replace(
            closeup,
            requirements=tuple(
                replace(r, expected_value="teal") if r.predicate == "color" else r
                for r in closeup.requirements
            ),
        )
    )
    assert changed.scene_fingerprint("closeup") != ir.scene_fingerprint("closeup")
    assert changed.scene_fingerprint("intro") == ir.scene_fingerprint("intro")
    assert changed.scene_fingerprint("outro") == ir.scene_fingerprint("outro")


def test_lookup_helpers(ir):
    assert [s.scene_key for s in ir.scenes_with_entity("cup")] == ["intro", "closeup"]
    assert ir.entity("cup").kind is EntityKind.PROP
    assert len(list(ir.iter_requirements())) == 8
    with pytest.raises(NotFound):
        ir.entity("ghost")
    with pytest.raises(NotFound):
        ir.scene("ghost")


def test_requirement_key_is_stable():
    assert RequirementSpec("intro", "color", "red", "cup").key == "intro:cup:color"
    assert RequirementSpec("intro", "mood", "calm").key == "intro:-:mood"


def test_scene_spec_duration():
    assert SceneSpec("s", 1000, 4500).duration_ms == 3500
