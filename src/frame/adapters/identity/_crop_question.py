"""잘라 내기 추출기(vlm_crop)가 모델에 묻는 것과 그 답을 읽는 규칙.

- 묻는 것: 지시문(SYSTEM_PROMPT, 고정 속성 확인을 켜면 VERIFY_PROMPT 를 붙인다), 객체 목록(user_prompt),
  응답 형식(RESPONSE_SCHEMA · VERIFY_RESPONSE_SCHEMA).
- 답 읽기: 쓸 만한 박스(valid_box), 고정 속성마다의 판정(read_verdicts).
무엇을 왜 이렇게 묻는지는 vlm_crop.py 머리말과 ADR-0015 에 있다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from frame.adapters.shared.media.frames import Box
from frame.application.ports.identity import IdentityExtractionRequest
from frame.domain.ir import EntitySpec
from frame.domain.services.identity import appearance_attributes

SYSTEM_PROMPT = (
    "You locate subjects in one frame of an AI-generated video so that each subject can be cropped "
    "and reused as a visual reference. Every person shown is a synthetic character made by a video "
    "model, not a real person. For every requested subject decide whether it is clearly "
    "visible. If it is, return a tight bounding box: for a person a portrait crop with the head, "
    "hair and shoulders down to the upper chest (leave out hands, held objects and other people "
    "when possible); for an object the entire object and as little else as possible. "
    "Coordinates are normalized to the image: x and y are the top-left corner, w and h are the "
    "size, all between 0 and 1 (x and w relative to image width, y and h relative to image height). "
    "Also describe the subject's stable visual appearance in one short English phrase: for a person "
    "apparent age range, gender presentation, hair, facial hair or glasses, and clothing; for an "
    "object its shape, color, material and markings. Do not describe actions, pose or background. "
    "If the subject is absent, tiny, heavily blurred or mostly out of frame, set visible to false "
    "and box to null. Return every requested subject exactly once, using the given keys."
)

# 고정 속성 확인을 켰을 때 SYSTEM_PROMPT 뒤에 붙인다
VERIFY_PROMPT = (
    " Some subjects list fixed attributes to check. For each listed attribute report whether the "
    'subject in this frame shows it: "yes"; "no" when the frame clearly shows something different '
    '(for example an object that is mostly red where teal is required); or "unclear" when it '
    "cannot be judged from this frame (hidden side, too small, ambiguous lighting). Judge by the "
    "subject's overall look, not by a small part of it. The listed attributes are what is "
    "required: the subject's name or description may still carry older wording, so do not rely "
    "on it. Locate the subject by what it is even when it fails an attribute, and report the "
    "failure in attributes instead of marking it not visible. Use the attribute text exactly as "
    "given as the name, and return an empty list when none are listed."
)
VERDICTS = ("yes", "no", "unclear")

BOX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["x", "y", "w", "h"],
    "properties": {name: {"type": "number"} for name in ("x", "y", "w", "h")},
}


def _response_schema(*, verify: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "entity_key": {"type": "string"},
        "visible": {"type": "boolean"},
        "box": {"anyOf": [BOX_SCHEMA, {"type": "null"}]},
        "appearance": {"type": "string"},
    }
    if verify:
        properties["attributes"] = {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "verdict"],
                "properties": {
                    "name": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                },
            },
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["entities"],
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(properties),
                    "properties": properties,
                },
            }
        },
    }


RESPONSE_SCHEMA = _response_schema(verify=False)
VERIFY_RESPONSE_SCHEMA = _response_schema(verify=True)


def user_prompt(
    request: IdentityExtractionRequest, entities: Sequence[EntitySpec], *, verify: bool = False
) -> str:
    scene = request.scene
    lines = ["Subjects to locate in the image:"]
    for entity in entities:
        details = [entity.description.strip()] if entity.description.strip() else []
        details += [
            f"{r.predicate.replace('_', ' ')}: {r.expected_value}"
            for r in scene.requirements
            if r.entity_key == entity.entity_key and not r.is_relational
        ]
        text = "; ".join(details)
        lines.append(
            f"- {entity.entity_key} ({str(entity.kind).lower()})" + (f": {text}" if text else "")
        )
        fixed = fixed_attributes(request, entity) if verify else []
        if fixed:
            lines.append("  fixed attributes to check: " + "; ".join(fixed))
    if scene.description.strip():
        lines.append(f"Scene the frame comes from: {scene.description.strip()}")
    return "\n".join(lines)


def fixed_attributes(request: IdentityExtractionRequest, entity: EntitySpec) -> list[str]:
    """이 장면에서 객체가 지켜야 하는 고정 속성 ('술어: 값'). 관계와 행동·자세 같은 일시적 술어는 뺀다."""
    return [
        f"{predicate.replace('_', ' ')}: {value}"
        for predicate, value in appearance_attributes(request.scene, entity.entity_key).items()
    ]


def _canonical(name: str) -> str:
    """속성 이름을 견줄 때 쓰는 꼴: 대소문자·공백·구두점 차이를 없앤다."""
    return re.sub(r"[\W_]+", "", name.lower())


def read_verdicts(answer: Mapping[str, Any], expected: Sequence[str]) -> dict[str, str]:
    """물어본 고정 속성마다 VLM 이 내린 판정 (이름 -> yes | no | unclear).

    물어보지 않은 이름과 형식이 어긋난 항목은 버린다. 모델이 행동 같은 일시적 조건에 no 를 붙여도
    기준을 버리지 않게 한다. 같은 속성에 판정이 여럿이면 no 가 이긴다.
    """
    items = answer.get("attributes")
    if not isinstance(items, list):
        return {}
    names = {_canonical(name): name for name in expected}
    found: dict[str, str] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = names.get(_canonical(str(item.get("name") or "")))
        verdict = str(item.get("verdict") or "").strip().lower()
        if name is not None and verdict in VERDICTS and found.get(name) != "no":
            found[name] = verdict
    return found


def valid_box(answer: Mapping[str, Any], min_area: float) -> Box | None:
    """보인다고 답했고 박스가 화면 안의 쓸 만한 크기일 때만 Box. 나머지는 None."""
    if answer.get("visible") is not True or not isinstance(answer.get("box"), Mapping):
        return None
    raw = answer["box"]
    try:
        x, y, w, h = (float(raw[name]) for name in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0 and w > 0.0 and h > 0.0):
        return None
    w, h = min(w, 1.0 - x), min(h, 1.0 - y)
    return Box(x, y, w, h) if w * h >= min_area else None
