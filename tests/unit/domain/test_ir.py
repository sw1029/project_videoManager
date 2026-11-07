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


# ---- 2차 검토에서 고친 스키마 규칙: 요구조건 키 · 관계 · 타임라인 · schema_version ------------


def test_relation_key_includes_related_entity_so_two_relations_can_coexist():
    a = RequirementSpec("intro", "holds", "cup", "barista", "cup")
    b = RequirementSpec("intro", "holds", "phone", "barista", "phone")
    assert a.key == "intro:barista:holds:cup"
    assert a.key != b.key
    ir = IntermediateRepresentation(
        entities=(EntitySpec("barista"), EntitySpec("cup"), EntitySpec("phone")),
        scenes=(SceneSpec("intro", 0, 1000, "", ("barista", "cup", "phone"), (a, b)),),
    )
    ir.validate()


def test_duplicate_requirement_key_in_a_scene_is_rejected(ir):
    intro = ir.scene("intro")
    dup = ir.with_scene(
        replace(
            intro,
            requirements=intro.requirements + (RequirementSpec("intro", "color", "blue", "cup"),),
        )
    )
    with pytest.raises(ValidationError, match="중복"):
        dup.validate()


def test_predicate_is_normalized_on_construction():
    assert RequirementSpec("s", "Colour", "red", "cup").predicate == "color"
    assert RequirementSpec("s", "색상", "red", "cup").predicate == "color"
    assert RequirementSpec("s", "shirt color", "white", "p").predicate == "shirt_color"


def test_relational_predicate_requires_related_entity(ir):
    intro = ir.scene("intro")
    bad = ir.with_scene(
        replace(intro, requirements=(RequirementSpec("intro", "wears", "apron", "barista"),))
    )
    with pytest.raises(ValidationError, match="관계"):
        bad.validate()


def test_attribute_predicate_must_not_have_related_entity(ir):
    intro = ir.scene("intro")
    bad = ir.with_scene(
        replace(intro, requirements=(RequirementSpec("intro", "color", "cup", "barista", "cup"),))
    )
    with pytest.raises(ValidationError, match="관계"):
        bad.validate()


def test_relation_value_must_equal_related_entity(ir):
    intro = ir.scene("intro")
    bad = ir.with_scene(
        replace(intro, requirements=(RequirementSpec("intro", "holds", "bag", "barista", "cup"),))
    )
    with pytest.raises(ValidationError, match="상대 객체"):
        bad.validate()


def test_relation_value_defaults_to_related_entity():
    assert RequirementSpec("intro", "holds", "", "barista", "cup").expected_value == "cup"


def test_empty_expected_value_is_rejected(ir):
    intro = ir.scene("intro")
    bad = ir.with_scene(
        replace(intro, requirements=(RequirementSpec("intro", "color", "  ", "cup"),))
    )
    with pytest.raises(ValidationError, match="값"):
        bad.validate()


@pytest.mark.parametrize(
    ("spans", "message"),
    [
        ([(0, 5000), (3000, 8000)], "겹"),
        ([(0, 4000), (5000, 8000)], "비어"),
        ([(1000, 4000)], "0 ms"),
    ],
)
def test_scene_timeline_must_be_contiguous_from_zero(spans, message):
    scenes = tuple(
        SceneSpec(f"s{i}", start, end, "", ("cup",)) for i, (start, end) in enumerate(spans)
    )
    bad = IntermediateRepresentation(entities=(EntitySpec("cup"),), scenes=scenes)
    with pytest.raises(ValidationError, match=message):
        bad.validate()


def test_ir_total_duration(ir):
    assert ir.total_duration_ms == 12000


def test_schema_version_roundtrip_and_unknown_version_rejected(ir):
    data = ir.to_dict()
    assert data["schema_version"] == 1
    assert IntermediateRepresentation.from_dict(data) == ir
    data["schema_version"] = 99
    with pytest.raises(ValidationError, match="schema_version"):
        IntermediateRepresentation.from_dict(data)


# ---- 3차 보완: 객체 종류 ANIMAL · 값 정규화 · 배경과의 관계 금지 ----------------------------


def test_animal_kind_round_trips():
    ir = IntermediateRepresentation(
        entities=(EntitySpec("dog", EntityKind.ANIMAL, "강아지"),),
        scenes=(SceneSpec("play", 0, 4000, "", ("dog",)),),
    )
    ir.validate()
    assert (
        IntermediateRepresentation.from_dict(ir.to_dict()).entity("dog").kind is EntityKind.ANIMAL
    )


def test_values_are_normalized_on_construction():
    assert RequirementSpec("s", "outfit", "White_Shirt", "p").expected_value == "white shirt"
    assert RequirementSpec("s", "color", " RED ", "p").expected_value == "red"
    assert RequirementSpec("s", "text", "FRAME", "p").expected_value == "FRAME"


def test_relation_cannot_target_a_background(ir):
    intro = ir.scene("intro")
    bad = ir.with_scene(
        replace(
            intro,
            requirements=(RequirementSpec("intro", "near", "cafe", "barista", "cafe"),),
        )
    )
    with pytest.raises(ValidationError, match="배경"):
        bad.validate()


def test_background_cannot_be_relation_subject(ir):
    intro = ir.scene("intro")
    bad = ir.with_scene(
        replace(
            intro,
            requirements=(RequirementSpec("intro", "near", "cup", "cafe", "cup"),),
        )
    )
    with pytest.raises(ValidationError, match="배경"):
        bad.validate()
