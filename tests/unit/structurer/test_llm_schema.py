"""IR JSON Schema: strict 모드 호환(모든 필드 required, additionalProperties false) 과 IR.to_dict 왕복."""

import jsonschema
import pytest

from frame.adapters.structurer.llm_schema import IR_SCHEMA_NAME, ir_json_schema
from frame.domain.enums import EntityKind
from frame.domain.ir import IR_SCHEMA_VERSION, IntermediateRepresentation
from tests.support.factories import cafe_ir


def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _objects(item)


def test_schema_is_strict_mode_compatible():
    schema = ir_json_schema()
    objects = list(_objects(schema))
    assert len(objects) == 4  # root, entity, scene, requirement
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])


def test_cafe_ir_document_validates_against_schema():
    jsonschema.validate(cafe_ir().to_dict(), ir_json_schema())


def test_schema_pins_version_kinds_and_vocabulary():
    schema = ir_json_schema()
    assert IR_SCHEMA_NAME == "frame_ir"
    assert schema["properties"]["schema_version"]["enum"] == [IR_SCHEMA_VERSION]
    entity = schema["properties"]["entities"]["items"]
    assert entity["properties"]["kind"]["enum"] == [str(k) for k in EntityKind]
    requirement = schema["properties"]["scenes"]["items"]["properties"]["requirements"]["items"]
    assert "holds" in requirement["properties"]["predicate"]["description"]
    assert "color" in requirement["properties"]["predicate"]["description"]
    assert requirement["properties"]["entity_key"]["type"] == ["string", "null"]


@pytest.mark.parametrize(
    "bad", [{"entities": []}, {"schema_version": 1, "entities": [], "scenes": 3}]
)
def test_documents_missing_required_fields_are_rejected(bad):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, ir_json_schema())


def test_schema_round_trips_through_from_dict():
    document = cafe_ir().to_dict()
    jsonschema.validate(document, ir_json_schema())
    assert IntermediateRepresentation.from_dict(document) == cafe_ir()
