"""LLM 변경 요청 해석 (FRAME_CHANGE_INTERPRETER=llm, ADR-0019).

사용자가 글로 쓴 수정 요청과 지금 버전의 중간표현(객체·장면·요구조건)을 주고, 구조화된 변경 하나를 받는다.
- 객체는 번호가 아니라 IR 의 키로 가리키게 하고, 술어는 어휘 목록에서 고르게 한다.
- 모델이 고른 객체·장면·유지 요소는 IR 에 있는 것만 받아들인다 (없는 것은 버리고 notes 에 적는다).
  이 변경이 바꾸는 것 자체를 그대로 두라는 유지 요소도 빼고 notes 에 적는다.
  규칙 기반 해석기와 같은 규칙이다 (_reading).
- 읽을 수 없는 요청(understood=false)은 ChangeRequestNotUnderstood 로 올린다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from frame.application.errors import LLMBadResponse
from frame.application.ports.change_request import (
    ChangeRequestInterpretation,
    ChangeRequestInterpreter,
    ChangeRequestNotUnderstood,
)
from frame.application.ports.llm import LLMClient
from frame.domain.enums import ChangeScope, KeepAspect
from frame.domain.errors import ValidationError
from frame.domain.ir import IntermediateRepresentation
from frame.domain.revision import KeepItem
from frame.domain.vocabulary import DEFAULT_VOCABULARY, PredicateVocabulary

from ._reading import entity_in, known_entity, known_scenes, usable_keep, with_left_out

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You turn a user's request to modify an AI-generated short video into ONE structured change. "
    "You are given the video's structured description: subjects (each with a key), scenes (each "
    "with a key, time range and requirements) and the list of predicates that can be changed. "
    "The request may be written in Korean or English.\n"
    "Return:\n"
    "- understood: false if the request does not ask for a change you can express, or asks for "
    "several unrelated changes at once; then explain in notes and leave the other fields empty.\n"
    "- entity_key: the key of the subject to change, copied exactly from the subject list; null "
    "when the change is about the whole scene (place, lighting, mood, camera).\n"
    "- predicate: one name from the predicate list (for example color, texture, outfit, action).\n"
    "- new_value: the requested value as a short English phrase (translate color and material "
    "names to English: 청록색 -> teal). For a relation predicate give the key of the other "
    "subject.\n"
    '- scope: "SCENES" only if the user limits the change to particular scenes, then list '
    'them in scene_keys (keys copied from the scene list); "ALL_APPEARANCES" if the user asks '
    "for every scene where the subject appears; null if the user says nothing about it.\n"
    "- keep: what the user asks to leave unchanged. For a subject use its key with aspect "
    "APPEARANCE (the same individual or object and its look), ACTION (action and pose) or SHAPE "
    "(form and size). For the scene use entity_key null with BACKGROUND, LIGHTING or COMPOSITION "
    "(framing and camera). 'Keep the person' means APPEARANCE and ACTION of each person; 'keep "
    "the background' means BACKGROUND. Never list the aspect that the change itself alters. "
    "Leave empty if the user asks to keep nothing. Example: for '인물과 배경은 유지해줘' with one "
    "person `barista`, keep is [{aspect: APPEARANCE, entity_key: barista}, {aspect: ACTION, "
    "entity_key: barista}, {aspect: BACKGROUND, entity_key: null}].\n"
    "- notes: one short Korean sentence saying how you read the request."
)

_ASPECTS = [str(a) for a in KeepAspect]
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "understood",
        "entity_key",
        "predicate",
        "new_value",
        "scope",
        "scene_keys",
        "keep",
        "notes",
    ],
    "properties": {
        "understood": {"type": "boolean"},
        "entity_key": {"type": ["string", "null"]},
        "predicate": {"type": "string"},
        "new_value": {"type": "string"},
        "scope": {"type": ["string", "null"], "enum": ["SCENES", "ALL_APPEARANCES", None]},
        "scene_keys": {"type": "array", "items": {"type": "string"}},
        "keep": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["aspect", "entity_key"],
                "properties": {
                    "aspect": {"type": "string", "enum": _ASPECTS},
                    "entity_key": {"type": ["string", "null"]},
                },
            },
        },
        "notes": {"type": "string"},
    },
}


class LLMChangeRequestInterpreter(ChangeRequestInterpreter):
    def __init__(
        self,
        client: LLMClient,
        *,
        vocabulary: PredicateVocabulary = DEFAULT_VOCABULARY,
        timeout_s: float | None = 60.0,
    ) -> None:
        self._client = client
        self._vocabulary = vocabulary
        self._timeout_s = timeout_s

    def interpret(
        self, ir: IntermediateRepresentation, text: str, *, entity_key: str | None = None
    ) -> ChangeRequestInterpretation:
        completion = self._client.complete_json(
            SYSTEM_PROMPT,
            self._user_prompt(ir, text, entity_key),
            RESPONSE_SCHEMA,
            schema_name="change_request",
            timeout_s=self._timeout_s,
        )
        log.info(
            "변경 요청 해석 LLM 호출: model=%s 입력 %s 토큰, 출력 %s 토큰",
            completion.model,
            completion.input_tokens,
            completion.output_tokens,
        )
        try:
            data = json.loads(completion.text)
            if not isinstance(data, Mapping):
                raise TypeError("응답이 JSON 객체가 아닙니다")
        except (ValueError, TypeError) as exc:
            raise LLMBadResponse(f"변경 요청 해석 응답을 읽지 못했습니다: {exc}") from exc
        return self._interpretation(ir, data)

    def _user_prompt(
        self, ir: IntermediateRepresentation, text: str, entity_key: str | None
    ) -> str:
        lines = ["Subjects:"]
        for entity in ir.entities:
            description = f": {entity.description.strip()}" if entity.description.strip() else ""
            lines.append(f"- {entity.entity_key} ({str(entity.kind).lower()}){description}")
        lines.append("Scenes:")
        for scene in ir.scenes:
            start, end = scene.planned_start_ms / 1000, scene.planned_end_ms / 1000
            lines.append(
                f"- {scene.scene_key} ({start:g}s-{end:g}s): {scene.description.strip()} "
                f"[subjects: {', '.join(scene.entity_keys) or 'none'}]"
            )
            for r in scene.requirements:
                subject = r.entity_key or "scene"
                lines.append(f"    {subject}.{r.predicate} = {r.expected_value}")
        lines.append(
            "Predicates: "
            + ", ".join(
                f"{p.name}{' (relation)' if p.relational else ''}" for p in self._vocabulary.specs()
            )
        )
        if entity_key is not None and ir.has_entity(entity_key):
            lines.append(f"The user is editing this subject on screen: {entity_key}")
        lines.append("Request:")
        lines.append(text.strip())
        return "\n".join(lines)

    def _interpretation(
        self, ir: IntermediateRepresentation, data: Mapping[str, Any]
    ) -> ChangeRequestInterpretation:
        notes = " ".join(str(data.get("notes") or "").split())[:300]
        if data.get("understood") is not True:
            raise ChangeRequestNotUnderstood(
                notes or "변경 요청을 구조화된 변경으로 옮기지 못했습니다"
            )
        raw_entity = str(data.get("entity_key") or "").strip()
        entity = known_entity(ir, raw_entity, notes) if raw_entity else None
        predicate = str(data.get("predicate") or "").strip()
        value = " ".join(str(data.get("new_value") or "").split())
        if not predicate or not value:
            raise ChangeRequestNotUnderstood(notes or "바꿀 속성이나 새 값을 읽지 못했습니다")
        scene_keys, missing = known_scenes(ir, data.get("scene_keys") or ())
        asked, unreadable = self._keep_items(ir, data.get("keep") or ())
        keep, gone, clashing = usable_keep(
            ir, asked, entity_key=entity, predicate=predicate, new_value=value
        )
        scope_raw = data.get("scope")
        scope = ChangeScope(scope_raw) if scope_raw in ("SCENES", "ALL_APPEARANCES") else None
        if scope is ChangeScope.SCENES and not scene_keys:
            scope = None  # 장면을 짚지 못했으면 범위를 정하지 않은 것으로 본다
        return ChangeRequestInterpretation(
            entity_key=entity,
            predicate=predicate,
            new_value=value,
            scope=scope,
            scene_keys=scene_keys,
            keep=keep,
            notes=with_left_out(notes, [*missing, *unreadable, *gone], clashing),
        )

    @staticmethod
    def _keep_items(ir: IntermediateRepresentation, raws: Any) -> tuple[list[KeepItem], list[str]]:
        """모델이 적은 유지 요소를 읽는다: (읽은 것, 읽지 못해 뺀 것의 설명)."""
        items: list[KeepItem] = []
        unreadable: list[str] = []
        for raw in raws:
            if not isinstance(raw, Mapping):
                continue
            target = str(raw.get("entity_key") or "").strip() or None
            if target is not None:
                # 이 영상의 객체 키로 맞춘다. 맞는 객체가 없으면 그대로 두고 usable_keep 이 걸러 낸다
                target = entity_in(ir, target) or target
            aspect = str(raw.get("aspect"))
            if target is None and aspect == str(KeepAspect.APPEARANCE):
                # 객체 없이 '모습' 을 유지하라는 답은 장면의 모습, 곧 배경으로 읽는다.
                # 실제 모델이 '배경은 유지해줘' 를 이렇게 돌려준 적이 있다 (2차 e2e 1회차)
                aspect = str(KeepAspect.BACKGROUND)
            try:
                items.append(KeepItem(KeepAspect(aspect), target))
            except (ValueError, ValidationError):
                unreadable.append(f"유지 요소 {raw.get('aspect')}")
        return items, unreadable
