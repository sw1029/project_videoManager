"""LLM 시리즈 기획 (FRAME_SERIES_PLANNER=llm, ADR-0026): 테마와 이번 편의 요청에서 편마다 다른 내용을 쓴다.

- 모델은 자리마다의 장면 설명, 그 편에만 나오는 객체·조건, 말할 문장, 자리별 문구 값, 비어 있는 변수의 값을 낸다.
- 테마의 고정 요소(자리의 순서·길이, 고정 출연의 고정 속성, 스타일 조건, 문구의 자리·모양)는 모델에 알려 주기만 하고
  받지 않는다. build_episode_plan 이 넣는다. 그래서 모델이 그것을 바꿔도 편의 조건은 달라지지 않는다.
- 말할 문장에는 자리마다 글자 수의 상한을 준다 (그 자리에서 말할 수 있는 시간 × 테마의 말 빠르기).
  녹음은 사람이 하므로, 대본이 장면보다 길면 뒤에서 고칠 수 없다.
- 요청이 준 변수의 값은 그대로 쓴다. 모델이 다른 값을 내도 요청의 값이 이긴다.
- 낸 내용이 build_episode_plan 을 통과하지 못하면 오류를 되돌려 보내 다시 묻는다 (max_attempts).
  어휘에 없는 술어(모델이 지어낸 speaks·overlay_text 같은 것)와, 빠르기를 올려도 장면에 들어가지 않는
  말할 문장도 되돌려 보낸다 (첫 실호출에서 둘 다 나왔다).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from frame.application.ports.llm import JsonCompletion, LLMClient
from frame.application.ports.series import PlanningRequest, SeriesPlanner, SeriesPlanningError
from frame.domain.enums import EntityKind
from frame.domain.episode import (
    DEFAULT_POLICY,
    EpisodeContent,
    PlanPolicy,
    SlotContent,
    build_episode_plan,
)
from frame.domain.errors import DomainError, ValidationError
from frame.domain.ir import EntitySpec, RequirementSpec
from frame.domain.services.narration import DEFAULT_NARRATION_POLICY
from frame.domain.theme import Theme, VariableScope
from frame.domain.video_analysis import spoken_characters
from frame.domain.vocabulary import DEFAULT_VOCABULARY

PROMPT_VERSION = "2026-11-14.5"
SCHEMA_NAME = "episode_content"

_SCENE_LEVEL = ("location", "lighting", "mood", "camera", "time_of_day", "weather")
_PREDICATES = {
    "attributes": ", ".join(
        s.name
        for s in DEFAULT_VOCABULARY.specs()
        if not s.relational and not s.transient and s.name not in _SCENE_LEVEL
    ),
    "doing": ", ".join(s.name for s in DEFAULT_VOCABULARY.specs() if s.transient),
    "relations": ", ".join(s.name for s in DEFAULT_VOCABULARY.specs() if s.relational),
    "scene": ", ".join(_SCENE_LEVEL),
}

SYSTEM_PROMPT = f"""You write one new episode of an existing short-form video series.
You get the series theme (fixed cast, scene slots, style, on-screen text rules, narration tone) and
the request for this episode. Return ONLY JSON that matches the schema.

What stays fixed is added by the system, so do not repeat or change it: the slot order and
lengths, the fixed attributes of the cast, the style conditions, where on-screen text goes.

What you write, for every slot of the theme (same slot_key, same order, no extra slots):
- description: what the camera films in the scene in this episode (people, objects, actions,
  place), following the slot's purpose. One sentence, in the language of the request. It becomes
  the prompt of a video model, so never mention overlay text, titles, captions or prices in it,
  even when the slot's purpose does: the system adds those afterwards.
- entity_keys: episode-specific entities that appear in the scene (define them in `entities`), and
  any cast member you add to a slot where it is not already listed.
- requirements: conditions that belong to this episode only (the colour of today's product, what
  the host does). Use ONLY these predicate names, never invent one:
  object attributes ({_PREDICATES["attributes"]}) and what an entity does
  ({_PREDICATES["doing"]}) need entity_key; relations ({_PREDICATES["relations"]}) need
  entity_key and related_entity_key; scene-level conditions ({_PREDICATES["scene"]}) use
  entity_key null. What someone does is an "action" with the deed as its value
  ("waves hand to camera"), not a predicate of its own. Values are short lowercase English
  phrases (text and brand keep the exact spelling). Never restate a fixed attribute of the cast.
- narration: the sentence a person will record for this scene, in the narration language and tone
  of the theme, following the slot's narration purpose. It must be speakable within the scene:
  count the letters and digits (not spaces or punctuation) and stay within the character limit
  given for the slot. A person records it, so a line that is too long cannot be fixed later;
  prefer a short line. Use "" for
  a silent slot.
- variables: values for SLOT-scope variables in this slot (for example a per-scene caption).

Also:
- entities: episode-specific entities only (unique snake_case ASCII keys that name what the thing
  is, not how it looks). Never redefine a cast member.
- variables (top level): values for EPISODE-scope variables that the request left empty. Never
  change a value the request gives.
- Keep the episode different from the earlier episodes listed, but inside the same theme.
- Overlay text (titles, captions, prices) comes from the text rules: never put it into
  requirements. Text that is physically on an object in the scene (a label printed on a cup) is a
  text requirement on that entity, with the exact spelling as its value. Do not define a separate
  entity for such text, and state it in every slot where the object appears."""


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_PAIR = _object({"key": _STRING, "value": _STRING})

RESPONSE_SCHEMA: dict[str, Any] = _object(
    {
        "variables": {"type": "array", "items": _PAIR},
        "entities": {
            "type": "array",
            "items": _object(
                {
                    "entity_key": _STRING,
                    "kind": {"type": "string", "enum": [str(k) for k in EntityKind]},
                    "description": _STRING,
                }
            ),
        },
        "slots": {
            "type": "array",
            "items": _object(
                {
                    "slot_key": _STRING,
                    "description": _STRING,
                    "entity_keys": {"type": "array", "items": _STRING},
                    "requirements": {
                        "type": "array",
                        "items": _object(
                            {
                                "entity_key": {"type": ["string", "null"]},
                                "related_entity_key": {"type": ["string", "null"]},
                                "predicate": _STRING,
                                "value": _STRING,
                            }
                        ),
                    },
                    "narration": _STRING,
                    "variables": {"type": "array", "items": _PAIR},
                }
            ),
        },
    }
)


class LLMSeriesPlanner(SeriesPlanner):
    def __init__(
        self,
        client: LLMClient,
        *,
        max_attempts: int = 3,
        timeout_s: float | None = None,
        policy: PlanPolicy = DEFAULT_POLICY,
    ) -> None:
        self._client = client
        self._max_attempts = max(1, max_attempts)
        self._timeout_s = timeout_s
        self._policy = policy
        self.attempts = 0
        self.last_completion: JsonCompletion | None = None  # 디버깅·기록용 원본 응답

    def plan(self, request: PlanningRequest) -> EpisodeContent:
        request.theme.validate()
        if not request.brief.title.strip():
            raise ValidationError("편의 제목이 비어 있습니다")
        user = build_user_prompt(request, self._policy)
        prompt = user
        self.attempts = 0
        last_error = "알 수 없는 오류"
        for attempt in range(1, self._max_attempts + 1):
            self.attempts = attempt
            completion = self._client.complete_json(
                SYSTEM_PROMPT,
                prompt,
                RESPONSE_SCHEMA,
                schema_name=SCHEMA_NAME,
                timeout_s=self._timeout_s,
            )
            self.last_completion = completion
            try:
                content = parse_content(request.theme, json.loads(completion.text))
                plan = build_episode_plan(request.theme, request.brief, content, self._policy)
                _check_narration_fits(request.theme, plan.script, self._policy)
                return content
            except (ValueError, KeyError, TypeError, DomainError) as exc:
                last_error = (
                    exc.message if isinstance(exc, DomainError) else f"{type(exc).__name__}: {exc}"
                )
                prompt = (
                    f"{user}\n\nYour previous answer was rejected: {last_error}\n"
                    f"Previous answer:\n{completion.text[:4000]}\nReturn the corrected JSON only."
                )
        raise SeriesPlanningError(f"편 기획이 {self.attempts}회 시도 후 실패했습니다: {last_error}")


def build_user_prompt(request: PlanningRequest, policy: PlanPolicy = DEFAULT_POLICY) -> str:
    theme, brief = request.theme, request.brief
    pace = theme.narration.pace_cps or policy.default_pace_cps
    lines = [f"Series theme: {theme.title}"]
    if theme.summary:
        lines.append(f"Summary: {theme.summary}")
    lines.append("Fixed cast (added by the system):")
    for member in theme.cast:
        fixed = "; ".join(f"{c.predicate}: {c.value}" for c in member.fixed_attributes)
        lines.append(
            f"- {member.entity_key} ({str(member.kind).lower()}): {member.description}"
            + (f" — role: {member.role}" if member.role else "")
            + (f" — fixed: {fixed}" if fixed else "")
        )
    if not theme.cast:
        lines.append("- none")
    style = "; ".join(f"{c.predicate}: {c.value}" for c in theme.style.conditions)
    lines.append(f"Style (added by the system): {style or 'none'}")
    if theme.style.notes:
        lines.append(f"Style notes: {theme.style.notes}")
    lines.append("Scene slots (write every one, in this order):")
    for slot in theme.format.slots:
        budget = max(0, slot.duration_ms - policy.lead_in_ms - policy.tail_ms)
        limit = int(budget / 1000 * pace)
        beat = theme.narration.beat(slot.slot_key)
        parts = [
            f"- {slot.slot_key} ({slot.duration_ms / 1000:g}s): {slot.purpose}",
            f"cast: {', '.join(slot.cast_keys) or 'none'}",
        ]
        if slot.conditions:
            parts.append(
                "fixed: " + "; ".join(f"{c.predicate}: {c.value}" for c in slot.conditions)
            )
        if beat is not None and (beat.purpose or beat.example):
            parts.append(
                f'narration purpose: {beat.purpose or "-"} (source said: "{beat.example}")'
            )
        if theme.narration.present or beat is not None:
            parts.append(f"narration limit: {limit} characters")
        lines.append(" | ".join(parts))
    lines.append(
        "Narration: "
        + (
            f"language {theme.narration.language or 'same as the request'}, tone: "
            f"{theme.narration.tone or 'same as the source'}"
            if theme.narration.present
            else "the source had no speech; write narration only if the request asks for it"
        )
    )
    if theme.text_rules:
        lines.append("On-screen text rules (the system renders them from the variables):")
        for rule in theme.text_rules:
            where = ", ".join(rule.slot_keys) or "every slot"
            lines.append(
                f'- {rule.rule_key} ({str(rule.role).lower()}, {where}): "{rule.template}"'
            )
    if theme.variables:
        lines.append("Variables:")
        for variable in theme.variables:
            given = str(brief.variables.get(variable.key, "")).strip()
            scope = "per slot" if variable.scope is VariableScope.SLOT else "per episode"
            state = f'given: "{given}" (keep it)' if given else "not given: fill it in"
            lines.append(
                f"- {variable.key} ({scope}): {variable.description or '-'}"
                f' — in the source: "{variable.example}" — {state}'
            )
    if request.earlier:
        lines.append("Earlier episodes (do not repeat them):")
        lines += [f"- {e.title}: {e.topic or '-'}" for e in request.earlier]
    lines.append(f"\nRequest for this episode — title: {brief.title.strip()}")
    lines.append(f"Topic: {brief.topic.strip() or '(none: follow the title)'}")
    return "\n".join(lines)


def parse_content(theme: Theme, document: Any) -> EpisodeContent:
    """모델의 응답을 EpisodeContent 로 옮긴다. 형식이 틀리면 ValueError·ValidationError."""
    if not isinstance(document, dict):
        raise ValueError("응답이 JSON 객체가 아닙니다")
    slot_scoped = {v.key for v in theme.variables if v.scope is VariableScope.SLOT}
    slots = []
    for item in document.get("slots") or ():
        slot_key = str(item["slot_key"]).strip()
        requirements = []
        for raw in item.get("requirements") or ():
            predicate = DEFAULT_VOCABULARY.normalize(str(raw.get("predicate") or ""))
            if DEFAULT_VOCABULARY.spec(predicate) is None:
                raise ValueError(
                    f"unknown predicate '{predicate}' in slot '{slot_key}'. Use only the listed "
                    "predicate names: an action goes into 'action', text printed on an object into "
                    "'text', and overlay text is never a requirement"
                )
            related = raw.get("related_entity_key") or None
            value = str(raw.get("value") or "")
            if DEFAULT_VOCABULARY.is_relational(predicate) is True:
                if related is None:
                    raise ValueError(
                        f"relation '{predicate}' in slot '{slot_key}' needs related_entity_key"
                    )
                value = str(related)  # 관계의 값은 상대 객체 키다 (모델이 다른 말을 적어 와도)
            else:
                related = None  # 속성 조건은 상대 객체를 갖지 않는다
            requirements.append(
                RequirementSpec(
                    scene_key=slot_key,
                    predicate=predicate,
                    expected_value=value,
                    entity_key=raw.get("entity_key") or None,
                    related_entity_key=related,
                )
            )
        slots.append(
            SlotContent(
                slot_key=slot_key,
                description=" ".join(str(item.get("description") or "").split()),
                entity_keys=tuple(
                    str(k).strip() for k in item.get("entity_keys") or () if str(k).strip()
                ),
                requirements=tuple(requirements),
                narration=" ".join(str(item.get("narration") or "").split()),
                variables={
                    k: v for k, v in _pairs(item.get("variables")).items() if k in slot_scoped
                },
            )
        )
    entities = [
        EntitySpec(
            str(item["entity_key"]).strip(),
            EntityKind(str(item.get("kind") or EntityKind.OTHER).upper()),
            " ".join(str(item.get("description") or "").split()),
        )
        for item in document.get("entities") or ()
    ]
    slots, entities = _fold_printed_text(slots, entities)
    return EpisodeContent(tuple(slots), tuple(entities), _pairs(document.get("variables")))


def _fold_printed_text(
    slots: list[SlotContent], entities: list[EntitySpec]
) -> tuple[list[SlotContent], list[EntitySpec]]:
    """다른 객체 위에 있는(on) 글자 객체를 그 객체의 text 속성으로 옮긴다.

    모델은 '물건에 찍힌 글자는 그 물건의 text 조건' 이라고 일러도 글자를 따로 객체로 내곤 한다 (둘째 실호출).
    그러면 글자가 그 객체를 적은 장면에만 걸리고, 물건이 나오는 다른 장면에는 걸리지 않는다.
    다른 객체 위에 있지 않은 글자(간판)는 그대로 둔다.
    """
    texts = {e.entity_key for e in entities if e.kind is EntityKind.TEXT}
    carrier: dict[str, str] = {}  # 글자 객체 -> 그 글자가 찍힌 객체
    for slot in slots:
        for requirement in slot.requirements:
            if (
                requirement.entity_key in texts
                and requirement.predicate == "on"
                and requirement.related_entity_key
                and requirement.related_entity_key not in texts
            ):
                carrier.setdefault(str(requirement.entity_key), requirement.related_entity_key)
    if not carrier:
        return slots, entities
    folded: list[SlotContent] = []
    for slot in slots:
        requirements: list[RequirementSpec] = []
        for requirement in slot.requirements:
            if requirement.related_entity_key in carrier:
                continue  # 글자 객체를 향한 관계는 뜻을 잃는다
            if requirement.entity_key not in carrier:
                requirements.append(requirement)
            elif requirement.predicate in ("text", "brand"):
                requirements.append(
                    RequirementSpec(
                        slot.slot_key,
                        requirement.predicate,
                        requirement.expected_value,
                        carrier[str(requirement.entity_key)],
                    )
                )
        keys = [carrier.get(key, key) for key in slot.entity_keys]
        folded.append(
            replace(slot, entity_keys=tuple(dict.fromkeys(keys)), requirements=tuple(requirements))
        )
    return folded, [e for e in entities if e.entity_key not in carrier]


def _check_narration_fits(theme: Theme, script: Any, policy: PlanPolicy) -> None:
    """빠르기를 올려도 장면에 들어가지 않을 문장이 있으면 ValueError (모델에 되돌려 보낸다)."""
    pace = theme.narration.pace_cps or policy.default_pace_cps
    too_long = []
    for line in script:
        stretched = line.budget_ms * DEFAULT_NARRATION_POLICY.max_tempo
        if line.estimated_ms is not None and line.estimated_ms > stretched:
            limit = int(line.budget_ms / 1000 * pace)
            too_long.append(
                f"slot '{line.scene_key}' narration has {spoken_characters(line.text)} characters, "
                f"the limit is {limit}"
            )
    if too_long:
        raise ValueError(
            "narration is too long to be spoken within the scene: " + "; ".join(too_long)
        )


def _pairs(raw: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, Mapping) and str(item.get("key") or "").strip():
            pairs[str(item["key"]).strip()] = " ".join(str(item.get("value") or "").split())
    return pairs
