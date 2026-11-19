"""객체 추적기(opencv)가 따라갈 객체의 시작 위치를 VLM 에 물을 때 쓰는 것과 그 답을 읽는 규칙.

- 찾기(locate): 프레임 한 장 + 객체 목록 -> 객체마다 보이는지와 객체 전체를 감싸는 박스.
  정체성 기준 추출(vlm_crop)과 달리 인물도 몸 전체를 감싼다 — 재생 중 그 객체를 가리키는 박스라서.
- 확인(verify): 짚은 박스를 잘라 낸 이미지들(C1, C2, ...)과 각각 누구라고 짚었는지 -> 맞는지 yes / no / unclear.
  실측(2026-11-19)에서 VLM 박스 9개 중 4개가 엉뚱한 곳(하늘·바닥 반사·다른 부위)을 짚었다. 잘못된 박스로 시작하면
  추적기는 그 엉뚱한 곳을 끝까지 따라가므로, 따라가기 전에 거른다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from frame.adapters.shared.vlm.boxes import BOX_SCHEMA
from frame.adapters.shared.vlm.wording import predicate_words
from frame.domain.ir import EntitySpec, SceneSpec

VERDICTS = ("yes", "no", "unclear")

LOCATE_SYSTEM_PROMPT = (
    "You locate subjects in one frame of an AI-generated video so that a tracker can follow each "
    "subject through the following frames. Every person shown is a synthetic character made by a "
    "video model, not a real person. For every requested subject decide whether it is clearly "
    "visible. If it is, return a tight bounding box around the whole subject (for a person or an "
    "animal the whole visible body; for an object the entire object) with as little background "
    "as possible. Coordinates are normalized to the image: x and y are the top-left corner, w and "
    "h are the size, all between 0 and 1 (x and w relative to image width, y and h relative to "
    "image height). Small objects need especially careful boxes: put the box on the object itself, "
    "never on a reflection, a shadow, the sky or empty space near it. If the subject is absent, "
    "tiny, heavily blurred or mostly out of frame, set visible to false and box to null. Return "
    "every requested subject exactly once, using the given keys."
)

LOCATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entities"],
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["entity_key", "visible", "box"],
                "properties": {
                    "entity_key": {"type": "string"},
                    "visible": {"type": "boolean"},
                    "box": {"anyOf": [BOX_SCHEMA, {"type": "null"}]},
                },
            },
        }
    },
}

VERIFY_SYSTEM_PROMPT = (
    "You check crops cut out of one frame of an AI-generated video. Each crop (C1, C2, ...) is said "
    "to show one named subject. Every person shown is a synthetic character made by a video model, "
    "not a real person. Judge which subject the crop shows, not how faithfully it was drawn: the "
    "description only tells you which subject is meant. Judge every crop on its own (crops of one "
    'frame may overlap). Answer "yes" if the subject is the main thing in the crop, even when its '
    "colours, markings, outfit details, pose or art style differ somewhat from the description or "
    'it is partly cut off. Answer "no" only if the crop mainly shows something else (background, '
    "sky, floor, a reflection, another subject or only an unrelated part of something), and "
    '"unclear" if you cannot tell. In `seen` say in a few English words what the crop actually '
    "shows. Return every crop id exactly once."
)

VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["crops"],
    "properties": {
        "crops": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["crop_id", "verdict", "seen"],
                "properties": {
                    "crop_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "seen": {"type": "string"},
                },
            },
        }
    },
}


def subject_line(entity: EntitySpec, scene: SceneSpec) -> str:
    """'- cup (prop): red ceramic mug; color: red' — 설명과 이 장면에서 그 객체가 지켜야 하는 조건."""
    details = [entity.description.strip()] if entity.description.strip() else []
    details += [
        f"{predicate_words(r.predicate)}: {r.expected_value}"
        for r in scene.requirements
        if r.entity_key == entity.entity_key and not r.is_relational and r.expected_value
    ]
    text = "; ".join(details)
    return f"{entity.entity_key} ({str(entity.kind).lower()})" + (f": {text}" if text else "")


def locate_prompt(entities: Sequence[EntitySpec], scene: SceneSpec) -> str:
    lines = ["Subjects to locate in the image:"]
    lines += [f"- {subject_line(entity, scene)}" for entity in entities]
    if scene.description.strip():
        lines.append(f"Scene the frame comes from: {scene.description.strip()}")
    return "\n".join(lines)


def identity_line(entity: EntitySpec) -> str:
    """'hat (prop): silver cowboy hat' — 그 객체가 무엇인지만. 장면 요구조건(색·동작·관계)은 넣지 않는다."""
    text = entity.description.strip()
    return f"{entity.entity_key} ({str(entity.kind).lower()})" + (f": {text}" if text else "")


def verify_prompt(items: Sequence[tuple[str, EntitySpec]]) -> str:
    """items: (crop_id, 그 crop 이 보여야 하는 객체).

    확인은 '그 객체인가' 만 묻는다. 요구조건을 실으면 맞는 객체도 거절한다 — 2026-11-19 실측에서 아직 컵을 들지 않은 첫
    프레임의 바리스타가 'holds: cup' 때문에 no 를 받았다. 요구조건을 지켰는지는 검수기(vlm_requirement)가 본다.
    설명도 '어느 객체인지' 를 알리는 데만 쓴다 (시스템 글이 그렇게 이른다): 같은 실측에서 갈색 반점이 있는 개가 설명의
    'white dog' 와 다르다고 no 를 받았다. 설명대로 그려졌는지는 일관성 검수(vlm_consistency)가 본다.
    """
    lines = ["Crops and the subject each crop should show:"]
    lines += [f"- {crop_id}: {identity_line(entity)}" for crop_id, entity in items]
    return "\n".join(lines)
