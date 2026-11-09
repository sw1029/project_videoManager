"""LLM 구조화 프롬프트. 규칙은 domain/ir.py validate 와 같아야 하며, 어휘는 vocabulary 에서 자동으로 채운다.

PROMPT_VERSION 은 프롬프트가 바뀔 때마다 올린다 (캐시 키·골드셋 비교용).
"""

from __future__ import annotations

import json

from frame.application.ports.structurer import StructuringRequest
from frame.domain.enums import EntityKind
from frame.domain.ir import IR_SCHEMA_VERSION
from frame.domain.vocabulary import DEFAULT_VOCABULARY, PredicateVocabulary, ValueKind

PROMPT_VERSION = "2026-11-07.2"
DEFAULT_DURATION_MS = 8000
MIN_SCENE_MS = 3000  # 숏폼 장면 길이 하한 (장면 수 상한 계산용)

_SCENE_LEVEL = ("location", "lighting", "mood", "camera", "time_of_day", "weather")

_EXAMPLE_BRIEF = (
    "8초 카페 숏폼. 바리스타가 카운터에서 빨간 머그컵을 들어 보이고(4초), 이어서 컵 클로즈업(4초). "
    "컵에는 FRAME 이라고 적혀 있고 분위기는 아늑하게."
)


def _req(
    scene: str, predicate: str, value: str, entity: str | None = None, related: str | None = None
) -> dict:
    return {
        "scene_key": scene,
        "entity_key": entity,
        "related_entity_key": related,
        "predicate": predicate,
        "expected_value": value,
    }


_EXAMPLE_DOCUMENT = {
    "schema_version": IR_SCHEMA_VERSION,
    "entities": [
        {"entity_key": "barista", "kind": "PERSON", "description": "바리스타"},
        {"entity_key": "cup", "kind": "PROP", "description": "빨간 머그컵"},
    ],
    "scenes": [
        {
            "scene_key": "intro",
            "planned_start_ms": 0,
            "planned_end_ms": 4000,
            "description": "바리스타가 머그컵을 들어 보인다",
            "entity_keys": ["barista", "cup"],
            "requirements": [
                _req("intro", "color", "red", "cup"),
                _req("intro", "holds", "cup", "barista", "cup"),
                _req("intro", "action", "shows cup to camera", "barista"),
                _req("intro", "location", "cafe counter"),
                _req("intro", "mood", "cozy"),
            ],
        },
        {
            "scene_key": "closeup",
            "planned_start_ms": 4000,
            "planned_end_ms": 8000,
            "description": "머그컵 클로즈업",
            "entity_keys": ["cup"],
            "requirements": [
                _req("closeup", "color", "red", "cup"),
                _req("closeup", "text", "FRAME", "cup"),
                _req("closeup", "camera", "close up"),
                _req("closeup", "mood", "cozy"),
            ],
        },
    ],
}
_EXAMPLE_OUTPUT = json.dumps(_EXAMPLE_DOCUMENT, ensure_ascii=False, separators=(",", ":"))


def build_system_prompt(vocabulary: PredicateVocabulary = DEFAULT_VOCABULARY) -> str:
    attributes = ", ".join(
        s.name for s in vocabulary.specs() if not s.relational and s.name not in _SCENE_LEVEL
    )
    scene_level = ", ".join(s.name for s in vocabulary.specs() if s.name in _SCENE_LEVEL)
    relations = ", ".join(s.name for s in vocabulary.specs() if s.relational)
    literals = ", ".join(s.name for s in vocabulary.specs() if s.value_kind is ValueKind.LITERAL)
    kinds = ", ".join(str(k) for k in EntityKind)
    return f"""You convert a short-form video brief into a JSON intermediate representation (IR).
Return ONLY JSON that matches the provided schema (schema_version {IR_SCHEMA_VERSION}).

Rules
- entity_key and scene_key are unique snake_case ASCII identifiers. Descriptions use the brief's language.
- Entity kinds ({kinds}): PERSON for people, ANIMAL for animals, PROP for objects/products/logos,
  BACKGROUND for places and environments, TEXT for on-screen text, OTHER when nothing fits.
- Every entity must appear in at least one scene's entity_keys. Do not invent entities or conditions
  that the brief does not state or clearly imply.
- Scenes are in timeline order, start at 0 ms, are contiguous (each planned_start_ms equals the previous
  planned_end_ms) and end exactly at the target duration. Keep each scene at least {MIN_SCENE_MS} ms.
- Each requirement's entity_key and related_entity_key must be listed in that scene's entity_keys.
- Object attribute predicates: {attributes}.
- Scene-level predicates ({scene_level}) describe the whole scene: use entity_key null for them.
  Describe places with the scene-level predicate "location", not with relations to a BACKGROUND entity.
- Relation predicates: {relations}. They need related_entity_key and expected_value equal to it, and they
  connect PERSON/ANIMAL/PROP/TEXT entities only. Never use a BACKGROUND entity as the subject or object
  of a relation. Use relations only for static states; movements and verbs are "action" conditions on
  the actor (e.g. owner.action = "throws ball").
- Use the predicate names above; add a new snake_case predicate only when none fits.
- Values are short lowercase phrases with spaces, not underscores (e.g. "white shirt", "slow orbit").
  Predicates {literals} keep the exact on-screen spelling.
- At most one requirement per (scene, entity, predicate, related entity). Repeat a condition in every
  scene where it must hold (e.g. the cup stays red in every scene it appears in).

Example
Brief: {_EXAMPLE_BRIEF}
Output: {_EXAMPLE_OUTPUT}"""


def build_user_prompt(request: StructuringRequest, *, max_scenes: int | None = None) -> str:
    duration = request.target_duration_ms or DEFAULT_DURATION_MS
    references = "\n".join(
        f"- {r.image_uri} (entity: {r.entity_key or 'global style reference'})"
        for r in request.reference_images
    )
    scene_hint = f"Use at most {max_scenes} scenes." if max_scenes else ""
    return (
        f"Target duration: {duration} ms. {scene_hint}\n"
        f"Reference images:\n{references or '- none'}\n\n"
        f"Brief:\n{request.prompt.strip()}"
    )


def feedback_prompt(user_prompt: str, error_message: str, previous_text: str) -> str:
    """검증에 실패한 답을 모델에 되돌려 보내 고치게 한다 (자기수정 루프)."""
    return (
        f"{user_prompt}\n\n"
        f"Your previous answer was rejected: {error_message}\n"
        f"Previous answer:\n{previous_text[:4000]}\n"
        "Return the corrected JSON only."
    )


def max_scenes_for(request: StructuringRequest) -> int:
    duration = request.target_duration_ms or DEFAULT_DURATION_MS
    return max(1, duration // MIN_SCENE_MS)
