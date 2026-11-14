"""편(에피소드) 계획: 테마 + 이번 편의 내용 -> 그 편의 IR·녹음 대본·화면 문구 (ADR-0026).

기획(모델 또는 사람)은 편마다 다른 것만 낸다 (EpisodeContent): 자리마다의 장면 설명, 그 편에만 나오는 객체와 조건,
말할 문장, 자리별 문구 값. 테마의 고정 요소는 build_episode_plan 이 넣는다.
- 장면 = 테마의 자리 그대로 (순서·길이·키). 기획이 자리를 빼거나 더할 수 없다.
- 고정 출연의 고정 속성과 스타일의 장면 조건은 요구조건으로 들어간다. 기획이 같은 조건에 다른 값을 내면 버린다.
- 편 안에서 객체의 모습(색·재질·글자)은 그 객체가 나오는 모든 장면에서 같다. 기획이 한 장면에만 적은 모습은
  나머지 장면에도 건다 (_same_look_in_every_scene).
- 화면 문구는 IR 에 넣지 않는다. 생성 조건이 아니라 후반 합성 계획(OverlayItem)이다 (ADR-0027).
  문구를 고쳐도 장면의 조건(fingerprint)은 그대로라 영상은 다시 만들어지지 않는다.
- 녹음 대본(ScriptLine)은 자리마다 한 줄이고, 그 자리의 길이에서 앞뒤 여유를 뺀 시간이 말할 수 있는 예산이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from frame.domain.errors import NotFound, ValidationError
from frame.domain.ir import EntitySpec, IntermediateRepresentation, RequirementSpec, SceneSpec
from frame.domain.screen import Region, TextRole
from frame.domain.series.source_analysis import spoken_characters
from frame.domain.series.theme import TextStyle, Theme, VariableScope, render_template
from frame.domain.vocabulary import DEFAULT_VOCABULARY

# 문구 규칙에 자리가 없을 때 구실에 따라 두는 기본 자리. 아래쪽 20% 와 오른쪽 가장자리는 플랫폼의 버튼·설명이
# 덮는 곳이라 피한다.
DEFAULT_REGIONS: Mapping[TextRole, Region] = {
    TextRole.TITLE: Region(0.08, 0.08, 0.84, 0.09),
    TextRole.CAPTION: Region(0.08, 0.68, 0.84, 0.08),
    TextRole.LABEL: Region(0.08, 0.58, 0.84, 0.07),
    TextRole.PRICE: Region(0.2, 0.6, 0.6, 0.08),
    TextRole.CALL_TO_ACTION: Region(0.08, 0.7, 0.84, 0.08),
    TextRole.WATERMARK: Region(0.05, 0.03, 0.4, 0.04),
    TextRole.OTHER: Region(0.08, 0.68, 0.84, 0.08),
}


class OverlayKind(StrEnum):
    TEXT = "TEXT"  # 테마의 문구 규칙에서 온 화면 문구
    PATCH = "PATCH"  # 영상 안의 틀린 글자를 가리고 다시 쓴 것 (국소 수정이 더한다)


@dataclass(frozen=True)
class PlanPolicy:
    lead_in_ms: int = 200  # 장면이 시작하고 말이 나오기까지의 여유
    tail_ms: int = 200  # 말이 끝나고 장면이 바뀌기까지의 여유
    # 테마에 말 빠르기가 없을 때 대본 길이를 어림하는 값 (초당 글자 수). 낭독 속도 근처의 어림값이다
    default_pace_cps: float = 5.5


DEFAULT_POLICY = PlanPolicy()


@dataclass(frozen=True)
class EpisodeBrief:
    """이번 편에 대한 요청: 제목, 내용, 테마 변수의 값. 여기 준 변수의 값이 기준이다."""

    title: str
    topic: str = ""
    variables: Mapping[str, str] = field(default_factory=dict)
    seed: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "topic": self.topic,
            "variables": dict(self.variables),
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EpisodeBrief:
        return cls(
            title=str(data["title"]),
            topic=str(data.get("topic", "")),
            variables={str(k): str(v) for k, v in (data.get("variables") or {}).items()},
            seed=data.get("seed"),
        )


@dataclass(frozen=True)
class SlotContent:
    """자리 하나에 이번 편이 채우는 내용."""

    slot_key: str
    description: str = ""  # 장면 설명. 비우면 자리의 구실을 쓴다
    entity_keys: tuple[str, ...] = ()  # 이 장면에 더 나오는 객체 (편의 객체나 다른 고정 출연)
    requirements: tuple[RequirementSpec, ...] = ()  # 이 편만의 조건
    narration: str = ""  # 이 장면에서 말할 문장
    variables: Mapping[str, str] = field(default_factory=dict)  # 이 자리의 문구 값 (자리별 자막 등)


@dataclass(frozen=True)
class EpisodeContent:
    """편마다 다른 내용 전체. 기획이 내고 build_episode_plan 이 테마와 합친다."""

    slots: tuple[SlotContent, ...]
    entities: tuple[EntitySpec, ...] = ()  # 이 편에만 나오는 객체
    variables: Mapping[str, str] = field(
        default_factory=dict
    )  # 기획이 채운 변수 (요청이 준 값이 우선)


@dataclass(frozen=True)
class ScriptLine:
    """녹음 대본 한 줄: 어느 장면에서, 무엇을, 얼마 안에 말하는가."""

    line_key: str
    scene_key: str
    text: str
    budget_ms: int  # 그 장면에서 말할 수 있는 시간
    estimated_ms: int | None = None  # 말 빠르기로 어림한 길이

    @property
    def fits(self) -> bool | None:
        return None if self.estimated_ms is None else self.estimated_ms <= self.budget_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_key": self.line_key,
            "scene_key": self.scene_key,
            "text": self.text,
            "budget_ms": self.budget_ms,
            "estimated_ms": self.estimated_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ScriptLine:
        estimated = data.get("estimated_ms")
        return cls(
            line_key=str(data["line_key"]),
            scene_key=str(data["scene_key"]),
            text=str(data["text"]),
            budget_ms=int(data["budget_ms"]),
            estimated_ms=int(estimated) if estimated is not None else None,
        )


@dataclass(frozen=True)
class OverlayItem:
    """화면 문구 하나: 어느 장면의 언제(장면 안 시각), 어디에, 어떤 모양으로 무엇을 쓰는가."""

    overlay_id: str
    scene_key: str
    text: str
    start_ms: int
    end_ms: int
    region: Region
    style: TextStyle = field(default_factory=TextStyle)
    role: TextRole = TextRole.OTHER
    rule_key: str | None = None  # 테마의 어느 문구 규칙에서 왔는가
    kind: OverlayKind = OverlayKind.TEXT

    def validate(self, scene_duration_ms: int) -> None:
        if not self.text.strip():
            raise ValidationError(f"화면 문구 '{self.overlay_id}' 의 내용이 비어 있습니다")
        if self.start_ms < 0 or self.end_ms <= self.start_ms or self.end_ms > scene_duration_ms:
            raise ValidationError(
                f"화면 문구 '{self.overlay_id}' 의 시간 {self.start_ms}~{self.end_ms} ms 가 "
                f"장면 '{self.scene_key}' ({scene_duration_ms} ms) 를 벗어납니다"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "overlay_id": self.overlay_id,
            "scene_key": self.scene_key,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "region": self.region.to_dict(),
            "style": self.style.to_dict(),
            "role": str(self.role),
            "rule_key": self.rule_key,
            "kind": str(self.kind),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OverlayItem:
        return cls(
            overlay_id=str(data["overlay_id"]),
            scene_key=str(data["scene_key"]),
            text=str(data["text"]),
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
            region=Region.from_dict(data["region"]),
            style=TextStyle.from_dict(data.get("style")),
            role=TextRole(data.get("role", TextRole.OTHER)),
            rule_key=data.get("rule_key"),
            kind=OverlayKind(data.get("kind", OverlayKind.TEXT)),
        )


@dataclass(frozen=True)
class EpisodePlan:
    """편 하나의 계획: 생성 조건(IR) + 녹음 대본 + 화면 문구."""

    title: str
    ir: IntermediateRepresentation
    script: tuple[ScriptLine, ...] = ()
    overlays: tuple[OverlayItem, ...] = ()
    variables: Mapping[str, str] = field(default_factory=dict)  # 편 단위 변수의 값
    notes: tuple[str, ...] = ()  # 계획을 만들며 고치거나 버린 것
    seed: int | None = None

    def overlays_for(self, scene_key: str) -> tuple[OverlayItem, ...]:
        return tuple(o for o in self.overlays if o.scene_key == scene_key)

    def overlay(self, overlay_id: str) -> OverlayItem:
        for item in self.overlays:
            if item.overlay_id == overlay_id:
                return item
        raise NotFound(f"화면 문구 '{overlay_id}' 가 계획에 없습니다", overlay_id=overlay_id)

    def line(self, line_key: str) -> ScriptLine:
        for line in self.script:
            if line.line_key == line_key:
                return line
        raise NotFound(f"대본 줄 '{line_key}' 이 계획에 없습니다", line_key=line_key)

    def with_overlay(self, item: OverlayItem) -> EpisodePlan:
        """같은 id 의 문구를 바꾸거나, 없으면 더한 새 계획. 장면과 시간이 맞아야 한다."""
        item.validate(self.ir.scene(item.scene_key).duration_ms)
        if any(o.overlay_id == item.overlay_id for o in self.overlays):
            overlays = tuple(item if o.overlay_id == item.overlay_id else o for o in self.overlays)
        else:
            overlays = (*self.overlays, item)
        return replace(self, overlays=overlays)

    def without_overlay(self, overlay_id: str) -> EpisodePlan:
        self.overlay(overlay_id)  # 없으면 NotFound
        return replace(self, overlays=tuple(o for o in self.overlays if o.overlay_id != overlay_id))

    def validate(self) -> None:
        self.ir.validate()
        durations = {scene.scene_key: scene.duration_ms for scene in self.ir.scenes}
        ids = [o.overlay_id for o in self.overlays]
        if len(ids) != len(set(ids)):
            raise ValidationError(f"화면 문구의 id 가 중복됩니다: {ids}")
        for item in self.overlays:
            if item.scene_key not in durations:
                raise ValidationError(
                    f"화면 문구 '{item.overlay_id}' 의 장면 '{item.scene_key}' 이 편에 없습니다"
                )
            item.validate(durations[item.scene_key])
        keys = [line.line_key for line in self.script]
        if len(keys) != len(set(keys)):
            raise ValidationError(f"대본 줄의 키가 중복됩니다: {keys}")
        for line in self.script:
            if line.scene_key not in durations:
                raise ValidationError(
                    f"대본 줄 '{line.line_key}' 의 장면 '{line.scene_key}' 이 편에 없습니다"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "ir": self.ir.to_dict(),
            "script": [line.to_dict() for line in self.script],
            "overlays": [item.to_dict() for item in self.overlays],
            "variables": dict(self.variables),
            "notes": list(self.notes),
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EpisodePlan:
        return cls(
            title=str(data["title"]),
            ir=IntermediateRepresentation.from_dict(data["ir"]),
            script=tuple(ScriptLine.from_dict(line) for line in data.get("script", ())),
            overlays=tuple(OverlayItem.from_dict(item) for item in data.get("overlays", ())),
            variables={str(k): str(v) for k, v in (data.get("variables") or {}).items()},
            notes=tuple(str(n) for n in data.get("notes", ())),
            seed=data.get("seed"),
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
        if line.fits is False:
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
