"""IR JSON Schema (구조화 출력용). IR.to_dict() 형식과 1:1 이며 strict 모드 규칙을 만족한다.

strict 모드 규칙: 모든 객체에 additionalProperties=false, properties 전부 required, 선택 필드는 null 허용 타입.
"""

from __future__ import annotations

from typing import Any

from frame.domain.enums import EntityKind
from frame.domain.ir import IR_SCHEMA_VERSION
from frame.domain.vocabulary import DEFAULT_VOCABULARY, PredicateVocabulary

IR_SCHEMA_NAME = "frame_ir"


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def ir_json_schema(vocabulary: PredicateVocabulary = DEFAULT_VOCABULARY) -> dict[str, Any]:
    attributes = ", ".join(s.name for s in vocabulary.specs() if not s.relational)
    relations = ", ".join(s.name for s in vocabulary.specs() if s.relational)
    requirement = _object(
        {
            "scene_key": {
                "type": "string",
                "description": "scene_key of the scene this belongs to",
            },
            "entity_key": {
                "type": ["string", "null"],
                "description": "target entity_key, or null for a scene-level condition (mood, lighting, camera)",
            },
            "related_entity_key": {
                "type": ["string", "null"],
                "description": "the other entity_key for relation predicates, otherwise null",
            },
            "predicate": {
                "type": "string",
                "description": (
                    f"attribute predicates: {attributes}. relation predicates: {relations}. "
                    "snake_case; use these names whenever one fits"
                ),
            },
            "expected_value": {
                "type": "string",
                "description": "required value; for relation predicates, equal to related_entity_key",
            },
        }
    )
    entity = _object(
        {
            "entity_key": {"type": "string", "description": "unique snake_case ascii identifier"},
            "kind": {"type": "string", "enum": [str(k) for k in EntityKind]},
            "description": {
                "type": "string",
                "description": "short visual description in the brief's language",
            },
        }
    )
    scene = _object(
        {
            "scene_key": {"type": "string", "description": "unique snake_case ascii identifier"},
            "planned_start_ms": {"type": "integer"},
            "planned_end_ms": {"type": "integer"},
            "description": {"type": "string", "description": "what happens in the scene"},
            "entity_keys": {"type": "array", "items": {"type": "string"}},
            "requirements": {"type": "array", "items": requirement},
        }
    )
    return _object(
        {
            "schema_version": {"type": "integer", "enum": [IR_SCHEMA_VERSION]},
            "entities": {"type": "array", "items": entity},
            "scenes": {"type": "array", "items": scene},
        }
    )
