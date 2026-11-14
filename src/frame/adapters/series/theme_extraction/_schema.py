"""테마 추출 응답의 JSON 스키마 (ADR-0025). strict 모드의 서버는 이 모양만 돌려준다."""

from __future__ import annotations

from typing import Any

from frame.domain.enums import EntityKind
from frame.domain.screen import TextRole, TextSource
from frame.domain.series.theme import (
    VariableScope,
)


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}


_NULLABLE_STRING = {"type": ["string", "null"]}


_CONDITION = _object({"predicate": _STRING, "value": _STRING})


_BOX = {
    "anyOf": [
        _object(
            {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "w": {"type": "number"},
                "h": {"type": "number"},
            }
        ),
        {"type": "null"},
    ]
}


RESPONSE_SCHEMA: dict[str, Any] = _object(
    {
        "title": _STRING,
        "summary": _STRING,
        "entities": {
            "type": "array",
            "items": _object(
                {
                    "entity_key": _STRING,
                    "kind": {"type": "string", "enum": [str(k) for k in EntityKind]},
                    "description": _STRING,
                    "recurring": {"type": "boolean"},
                    "role": _STRING,
                    "fixed_attributes": {"type": "array", "items": _CONDITION},
                }
            ),
        },
        "slots": {
            "type": "array",
            "items": _object(
                {
                    "slot": _STRING,
                    "purpose": _STRING,
                    "description": _STRING,
                    "entity_keys": {"type": "array", "items": _STRING},
                    "requirements": {
                        "type": "array",
                        "items": _object(
                            {
                                "entity_key": _NULLABLE_STRING,
                                "related_entity_key": _NULLABLE_STRING,
                                "predicate": _STRING,
                                "value": _STRING,
                            }
                        ),
                    },
                    "narration_purpose": _STRING,
                }
            ),
        },
        "style": _object(
            {
                "conditions": {"type": "array", "items": _CONDITION},
                "palette": {"type": "array", "items": _STRING},
                "notes": _STRING,
            }
        ),
        "screen_texts": {
            "type": "array",
            "items": _object(
                {
                    "text": _STRING,
                    "slot": _STRING,
                    "role": {"type": "string", "enum": [str(r) for r in TextRole]},
                    "source": {"type": "string", "enum": [str(s) for s in TextSource]},
                    "box": _BOX,
                    "style": _STRING,
                }
            ),
        },
        "text_rules": {
            "type": "array",
            "items": _object(
                {
                    "rule_key": _STRING,
                    "role": {"type": "string", "enum": [str(r) for r in TextRole]},
                    "template": _STRING,
                    "slots": {"type": "array", "items": _STRING},
                    "box": _BOX,
                    "color": _STRING,
                    "background": _NULLABLE_STRING,
                    "example": _STRING,
                }
            ),
        },
        "variables": {
            "type": "array",
            "items": _object(
                {
                    "key": _STRING,
                    "description": _STRING,
                    "example": _STRING,
                    "scope": {"type": "string", "enum": [str(s) for s in VariableScope]},
                }
            ),
        },
        "narration_tone": _STRING,
        "music": _object({"present": {"type": ["boolean", "null"]}, "mood": _STRING}),
    }
)
