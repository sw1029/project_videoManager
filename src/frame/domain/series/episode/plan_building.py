"""편 계획 만들기: 테마 + 이번 편의 내용 -> 그 편의 IR·녹음 대본·화면 문구 (ADR-0026). 순수 함수.

기획은 편마다 다른 것만 낸다. 테마의 고정 요소는 build_episode_plan 이 넣는다.
- 장면 = 테마의 자리 그대로 (순서·길이·키). 기획이 자리를 빼거나 더할 수 없다.
- 고정 출연의 고정 속성과 스타일의 장면 조건은 요구조건으로 들어간다. 기획이 같은 조건에 다른 값을 내면 버린다.
- 편 안에서 객체의 모습(색·재질·글자)은 그 객체가 나오는 모든 장면에서 같다 (_same_look_in_every_scene).
- 녹음 대본은 자리마다 한 줄이고, 그 자리의 길이에서 앞뒤 여유를 뺀 시간이 말할 수 있는 예산이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from frame.domain.errors import ValidationError
from frame.domain.ir import EntitySpec, IntermediateRepresentation, RequirementSpec, SceneSpec
from frame.domain.series.theme import Theme, VariableScope, render_template
from frame.domain.speech import spoken_characters
from frame.domain.vocabulary import DEFAULT_VOCABULARY

from .plan import (
    DEFAULT_POLICY,
    DEFAULT_REGIONS,
    EpisodeBrief,
    EpisodeContent,
    EpisodePlan,
    OverlayItem,
    PlanPolicy,
    ScriptLine,
    SlotContent,
)


def build_episode_plan(
    theme: Theme,
    brief: EpisodeBrief,
    content: EpisodeContent,
    policy: PlanPolicy = DEFAULT_POLICY,
) -> EpisodePlan:
    """테마의 고정 요소와 이번 편의 내용을 합쳐 편의 계획을 만든다. 규칙을 어긴 내용은 ValidationError."""
    theme.validate()
    if not brief.title.strip():
        raise ValidationError("편의 제목이 비어 있습니다")
    notes: list[str] = []
    values = _episode_values(theme, brief, content, notes)
    by_slot = _slot_contents(theme, content)
    extras = _extra_entities(theme, content)

    scenes: list[SceneSpec] = []
    used: list[str] = []
    for slot in theme.format.slots:
        filled = by_slot[slot.slot_key]
        entity_keys = _entity_keys(theme, slot.slot_key, slot.cast_keys, filled, extras)
        used += [key for key in entity_keys if key not in used]
        start, end = theme.format.span(slot.slot_key)
        scenes.append(
            SceneSpec(
                scene_key=slot.slot_key,
                planned_start_ms=start,
                planned_end_ms=end,
                description=filled.description.strip() or slot.purpose,
                entity_keys=entity_keys,
                requirements=_requirements(theme, slot.slot_key, entity_keys, filled, notes),
            )
        )
    scenes = _same_look_in_every_scene(scenes, notes)
    unused = [key for key in extras if key not in used]
    if unused:
        notes.append(f"어느 장면에도 나오지 않는 객체를 뺐습니다: {unused}")
    entities = tuple(m.entity_spec() for m in theme.cast if m.entity_key in used) + tuple(
        extras[key] for key in extras if key in used
    )
    ir = IntermediateRepresentation(entities=entities, scenes=tuple(scenes))
    ir.validate()
    plan = EpisodePlan(
        title=brief.title.strip(),
        ir=ir,
        script=_script(theme, by_slot, policy, notes),
        overlays=_overlays(theme, by_slot, values),
        variables=values,
        notes=tuple(notes),
        seed=brief.seed,
    )
    plan.validate()
    return plan


# ---- 변수 -------------------------------------------------------------------
def _episode_values(
    theme: Theme, brief: EpisodeBrief, content: EpisodeContent, notes: list[str]
) -> dict[str, str]:
    """편 단위 변수의 값. 요청이 준 값이 기획이 낸 값보다 우선한다."""
    episode_keys = [v.key for v in theme.variables if v.scope is VariableScope.EPISODE]
    unknown = sorted(set(brief.variables) - {v.key for v in theme.variables})
    if unknown:
        raise ValidationError(f"테마에 없는 변수입니다: {unknown}", variables=tuple(unknown))
    values: dict[str, str] = {}
    for key in episode_keys:
        asked = str(brief.variables.get(key, "")).strip()
        planned = str(content.variables.get(key, "")).strip()
        if asked and planned and asked != planned:
            notes.append(
                f"변수 '{key}': 기획이 낸 값 '{planned}' 대신 요청의 값 '{asked}' 를 씁니다"
            )
        if asked or planned:
            values[key] = asked or planned
    needed = {name for rule in theme.text_rules for name in rule.variables if name in episode_keys}
    missing = sorted(needed - set(values))
    if missing:
        raise ValidationError(
            f"화면 문구에 쓰는 변수에 값이 없습니다: {missing}", variables=tuple(missing)
        )
    return values


# ---- 장면 -------------------------------------------------------------------
def _slot_contents(theme: Theme, content: EpisodeContent) -> dict[str, SlotContent]:
    keys = [slot.slot_key for slot in content.slots]
    if len(keys) != len(set(keys)):
        raise ValidationError(f"편의 내용에 같은 자리가 두 번 나옵니다: {keys}")
    expected = [slot.slot_key for slot in theme.format.slots]
    missing = [key for key in expected if key not in keys]
    unknown = [key for key in keys if key not in expected]
    if missing or unknown:
        raise ValidationError(
            f"편의 내용은 테마의 자리를 모두, 그 자리만 채워야 합니다 (빠진 자리 {missing}, 없는 자리 {unknown})",
            missing=tuple(missing),
            unknown=tuple(unknown),
        )
    return {slot.slot_key: slot for slot in content.slots}


def _extra_entities(theme: Theme, content: EpisodeContent) -> dict[str, EntitySpec]:
    extras: dict[str, EntitySpec] = {}
    for entity in content.entities:
        if theme.has_cast(entity.entity_key):
            raise ValidationError(
                f"'{entity.entity_key}' 는 테마의 고정 출연입니다. 편에서 다시 정의할 수 없습니다"
            )
        if entity.entity_key in extras:
            raise ValidationError(f"편의 객체 키가 중복됩니다: {entity.entity_key}")
        extras[entity.entity_key] = entity
    return extras


def _entity_keys(
    theme: Theme,
    slot_key: str,
    cast_keys: Sequence[str],
    filled: SlotContent,
    extras: Mapping[str, EntitySpec],
) -> tuple[str, ...]:
    keys = list(cast_keys)
    for key in filled.entity_keys:
        if key in keys:
            continue
        if not theme.has_cast(key) and key not in extras:
            raise ValidationError(f"장면 '{slot_key}' 이 정의되지 않은 객체를 가리킵니다: '{key}'")
        keys.append(key)
    return tuple(keys)


def _requirements(
    theme: Theme,
    slot_key: str,
    entity_keys: Sequence[str],
    filled: SlotContent,
    notes: list[str],
) -> tuple[RequirementSpec, ...]:
    fixed = theme.fixed_requirements(slot_key, entity_keys)
    by_key = {r.key: r for r in fixed}
    merged = list(fixed)
    for requirement in filled.requirements:
        if requirement.scene_key != slot_key:
            raise ValidationError(
                f"장면 '{slot_key}' 의 조건이 다른 장면 '{requirement.scene_key}' 을 가리킵니다"
            )
        existing = by_key.get(requirement.key)
        if existing is None:
            by_key[requirement.key] = requirement
            merged.append(requirement)
        elif existing.expected_value != requirement.expected_value:
            owner = "테마의 고정값" if existing in fixed else "앞의 조건"
            notes.append(
                f"장면 '{slot_key}' 의 조건 {requirement.key} = '{requirement.expected_value}' 를 버렸습니다 "
                f"({owner} '{existing.expected_value}' 가 우선)"
            )
    return tuple(merged)


def _same_look_in_every_scene(scenes: Sequence[SceneSpec], notes: list[str]) -> list[SceneSpec]:
    """객체의 모습(색·재질·글자 같은 고정 속성)을 그 객체가 나오는 모든 장면에 같게 건다.

    - 한 장면에만 적힌 모습은 그 객체가 나오는 나머지 장면에도 건다.
    - 같은 속성에 장면마다 다른 값이 적혀 있으면 먼저 나온 장면의 값을 쓴다.
    - 관계, 장면마다 달라지는 것(행동·자세·표정·위치), 장면 수준 조건은 옮기지 않는다.
    둘째 실호출에서 컵의 글자가 클로즈업 장면에만 걸려, 손에 든 장면의 컵에는 글자가 없이 생성됐다
    (같은 컵이 장면마다 달라 일관성 검수가 FAIL). 정체성 기준(ADR-0015)도 장면마다 모습 조건이 같아야 맞는다.
    """
    looks: dict[tuple[str, str], str] = {}  # (객체, 술어) -> 먼저 나온 값
    for scene in scenes:
        for requirement in scene.requirements:
            if _is_look(requirement):
                looks.setdefault(
                    (str(requirement.entity_key), requirement.predicate), requirement.expected_value
                )
    result: list[SceneSpec] = []
    for scene in scenes:
        merged: list[RequirementSpec] = []
        stated: set[tuple[str, str]] = set()
        for requirement in scene.requirements:
            if not _is_look(requirement):
                merged.append(requirement)
                continue
            key = (str(requirement.entity_key), requirement.predicate)
            stated.add(key)
            first = looks[key]
            if requirement.expected_value != first:
                notes.append(
                    f"장면 '{scene.scene_key}' 의 조건 {requirement.key} = '{requirement.expected_value}' 를 "
                    f"'{first}' 로 맞췄습니다 (같은 편에서 객체의 모습은 장면마다 같습니다. 먼저 나온 값을 씁니다)"
                )
                requirement = replace(requirement, expected_value=first)
            merged.append(requirement)
        for (entity_key, predicate), value in looks.items():
            if entity_key in scene.entity_keys and (entity_key, predicate) not in stated:
                merged.append(RequirementSpec(scene.scene_key, predicate, value, entity_key))
                notes.append(
                    f"객체 '{entity_key}' 의 {predicate} = '{value}' 를 그 객체가 나오는 "
                    f"장면 '{scene.scene_key}' 에도 걸었습니다"
                )
        result.append(replace(scene, requirements=tuple(merged)))
    return result


def _is_look(requirement: RequirementSpec) -> bool:
    """객체의 모습을 정하는 조건인가: 객체에 걸리고, 관계가 아니고, 장면마다 달라지는 것이 아니다."""
    return (
        requirement.entity_key is not None
        and not requirement.is_relational
        and not DEFAULT_VOCABULARY.is_transient(requirement.predicate)
    )


# ---- 녹음 대본 ----------------------------------------------------------------
def _script(
    theme: Theme, by_slot: Mapping[str, SlotContent], policy: PlanPolicy, notes: list[str]
) -> tuple[ScriptLine, ...]:
    pace = theme.narration.pace_cps or policy.default_pace_cps
    lines: list[ScriptLine] = []
    for slot in theme.format.slots:
        text = " ".join(by_slot[slot.slot_key].narration.split())
        if not text:
            continue
        budget = max(0, slot.duration_ms - policy.lead_in_ms - policy.tail_ms)
        estimated = round(spoken_characters(text) / pace * 1000) if pace > 0 else None
        line = ScriptLine(f"L{len(lines) + 1}", slot.slot_key, text, budget, estimated)
        if estimated is not None and line.fits is False:
            notes.append(
                f"대본 {line.line_key} (장면 '{slot.slot_key}'): 어림 {estimated / 1000:.1f}초가 "
                f"말할 수 있는 {budget / 1000:.1f}초보다 깁니다"
            )
        lines.append(line)
    return tuple(lines)


# ---- 화면 문구 ----------------------------------------------------------------
def _overlays(
    theme: Theme, by_slot: Mapping[str, SlotContent], values: Mapping[str, str]
) -> tuple[OverlayItem, ...]:
    slot_scoped = {v.key for v in theme.variables if v.scope is VariableScope.SLOT}
    items: list[OverlayItem] = []
    for rule in theme.text_rules:
        for slot in theme.format.slots:
            if rule.slot_keys and slot.slot_key not in rule.slot_keys:
                continue
            own = {
                key: str(value).strip()
                for key, value in by_slot[slot.slot_key].variables.items()
                if key in slot_scoped
            }
            spoken = " ".join(by_slot[slot.slot_key].narration.split())
            if rule.follows_speech and spoken and len(rule.variables) == 1:
                # 말을 받아 적는 자막 (ADR-0028): 기획이 준 값이 아니라 그 자리에서 말할 줄이 자막이다
                own[rule.variables[0]] = spoken
            available = {**values, **own}
            if any(not available.get(name) for name in rule.variables):
                continue  # 자리별 값이 없는 자리에는 그 문구를 넣지 않는다
            items.append(
                OverlayItem(
                    overlay_id=f"{slot.slot_key}:{rule.rule_key}",
                    scene_key=slot.slot_key,
                    text=render_template(rule.template, available),
                    start_ms=rule.start_ms or 0,
                    end_ms=rule.end_ms if rule.end_ms is not None else slot.duration_ms,
                    region=rule.region or DEFAULT_REGIONS[rule.role],
                    style=rule.style,
                    role=rule.role,
                    rule_key=rule.rule_key,
                )
            )
    return tuple(items)
