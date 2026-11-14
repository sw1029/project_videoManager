"""규칙 기반 구조화 어댑터 (결정적). LLM 어댑터의 대체재이자 테스트의 기준 구현.

입력 DSL (한 줄에 한 문장):

    scene <scene_key> <start_ms>-<end_ms> [: 설명]
    entity <entity_key> [<종류: EntityKind 의 값, PERSON·ANIMAL·PROP·BACKGROUND·TEXT·OTHER>] [: 설명]
    in <scene_key>: <entity_key>[, <entity_key> ...]
    <entity_key>.<predicate> = <value> [@ <scene_key>[, <scene_key> ...]]
    <entity_key> <predicate> -> <related_entity_key> [@ <scene_key>[, ...]]
    scene.<predicate> = <value> [@ <scene_key>[, ...]]        # 장면 수준 조건 (분위기·조명·카메라)

- '#' 부터 줄 끝까지는 주석이다. 다만 낱말 안의 '#'(A#1)과, '=' 바로 뒤에 값으로 쓴 HEX 색(#ff0000)은 값이다.
- '@' 가 없는 조건은 객체가 등장하는 모든 장면(장면 수준 조건은 모든 장면)에 적용된다.
- '@' 로 장면을 지정하면 해당 장면에 객체가 자동으로 연결된다. 장면 목록의 '@' 는 값과 띄어 쓴다.
  값에 붙어 있는 '@'(info@frame.io)는 값의 일부다.
- 술어는 어휘(domain/vocabulary.py)로 정규화된다: cup.색상 = 빨강 -> color, p 들다 -> cup -> holds.
- scene 문장이 하나도 없으면 target_duration_ms(기본 8000ms) 길이의 단일 장면을 만든다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from frame.application.errors import StructuringError
from frame.application.ports.structurer import RequirementStructurer, StructuringRequest
from frame.domain.enums import EntityKind
from frame.domain.errors import DomainError
from frame.domain.ir import EntitySpec, IntermediateRepresentation, RequirementSpec, SceneSpec

_SCENE = re.compile(r"^scene\s+(\S+)\s+(\d+)\s*-\s*(\d+)\s*(?::\s*(.*))?$")
# 종류 목록은 EntityKind 에서 만든다 (손으로 적으면 종류가 더해질 때 어긋난다)
_KINDS = "|".join(re.escape(str(kind)) for kind in EntityKind)
_ENTITY = re.compile(rf"^entity\s+(\S+)(?:\s+({_KINDS}))?\s*(?::\s*(.*))?$")
_IN = re.compile(r"^in\s+(\S+)\s*:\s*(.+)$")
# 장면 목록은 값과 띄어 쓴 '@' 뒤에 온다. 값에 붙은 '@' 는 값이다
_ATTR = re.compile(r"^([^\s.]+)\.(\S+)\s*=\s*(.+?)(?:\s+@\s*(.+))?$")
_REL = re.compile(r"^(\S+)\s+(\S+)\s*->\s*(\S+)\s*(?:@\s*(.+))?$")

DEFAULT_SCENE_KEY = "scene_01"
DEFAULT_DURATION_MS = 8000


# `scene.mood = calm` 처럼 객체 자리에 쓰면 장면 수준 조건
SCENE_LEVEL_TOKEN = "scene"  # noqa: S105 - 비밀이 아니라 DSL 의 낱말


@dataclass
class _PendingRequirement:
    entity_key: str | None  # None = 장면 수준 조건
    predicate: str
    value: str
    related_entity_key: str | None
    scene_keys: tuple[str, ...]  # 비어 있으면 '등장하는 모든 장면'
    line_no: int


@dataclass
class _Draft:
    scenes: dict[str, SceneSpec] = field(default_factory=dict)
    entities: dict[str, EntitySpec] = field(default_factory=dict)
    members: dict[str, list[str]] = field(
        default_factory=dict
    )  # scene_key -> entity_keys (순서 유지)
    requirements: list[_PendingRequirement] = field(default_factory=list)


# '=' 바로 뒤에 값으로 쓴 HEX 색 (#rgb · #rgba · #rrggbb · #rrggbbaa)
_HEX_VALUE = re.compile(r"=\s*#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?=\s|$)")


def _without_comment(raw: str) -> str:
    """주석을 뗀 한 줄. 주석은 줄 머리의 '#', 또는 공백 뒤의 '#' 부터 줄 끝까지다.

    낱말 안의 '#'(A#1)과 '=' 바로 뒤에 값으로 쓴 HEX 색(#ff0000)은 주석이 아니다.
    """
    line = raw.strip()
    values = [(m.start(), m.end()) for m in _HEX_VALUE.finditer(line)]
    for at, char in enumerate(line):
        if char != "#":
            continue
        if at == 0:
            return ""
        if not line[at - 1].isspace() or any(start < at < end for start, end in values):
            continue
        return line[:at].strip()
    return line


class RuleBasedStructurer(RequirementStructurer):
    def structure(self, request: StructuringRequest) -> IntermediateRepresentation:
        draft = self._parse(request.prompt)
        if not draft.entities:
            raise StructuringError("객체(entity) 가 하나도 없습니다")
        if not draft.scenes:
            duration = request.target_duration_ms or DEFAULT_DURATION_MS
            draft.scenes[DEFAULT_SCENE_KEY] = SceneSpec(DEFAULT_SCENE_KEY, 0, duration)
            draft.members[DEFAULT_SCENE_KEY] = list(draft.entities)
        ir = self._build(draft)
        try:
            ir.validate()
        except DomainError as exc:
            raise StructuringError(
                f"구조화 결과가 무결성 규칙을 위반합니다: {exc.message}"
            ) from exc
        return ir

    # ---- 1단계: 줄 단위 파싱 -------------------------------------------------
    def _parse(self, prompt: str) -> _Draft:
        draft = _Draft()
        if not prompt.strip():
            raise StructuringError("요구사항이 비어 있습니다")
        for line_no, raw in enumerate(prompt.splitlines(), start=1):
            line = _without_comment(raw)
            if not line:
                continue
            if m := _SCENE.match(line):
                key, start, end, desc = m.groups()
                if key in draft.scenes:
                    raise StructuringError(f"장면 '{key}' 이 중복 정의되었습니다 (줄 {line_no})")
                draft.scenes[key] = SceneSpec(key, int(start), int(end), (desc or "").strip())
                draft.members.setdefault(key, [])
            elif m := _ENTITY.match(line):
                key, kind, desc = m.groups()
                if key in draft.entities:
                    raise StructuringError(f"객체 '{key}' 이 중복 정의되었습니다 (줄 {line_no})")
                draft.entities[key] = EntitySpec(
                    key, EntityKind(kind) if kind else EntityKind.OTHER, (desc or "").strip()
                )
            elif m := _IN.match(line):
                scene_key, csv = m.groups()
                for entity_key in self._split(csv):
                    self._link(draft, scene_key, entity_key, line_no)
            elif m := _ATTR.match(line):
                entity_key, predicate, value, scenes = m.groups()
                draft.requirements.append(
                    _PendingRequirement(
                        None if entity_key == SCENE_LEVEL_TOKEN else entity_key,
                        predicate,
                        value.strip(),
                        None,
                        self._scene_list(scenes),
                        line_no,
                    )
                )
            elif m := _REL.match(line):
                entity_key, predicate, related, scenes = m.groups()
                draft.requirements.append(
                    _PendingRequirement(
                        entity_key, predicate, related, related, self._scene_list(scenes), line_no
                    )
                )
            else:
                raise StructuringError(f"해석할 수 없는 문장 (줄 {line_no}): {line!r}")
        return draft

    # ---- 2단계: 요구조건을 장면에 배치 ------------------------------------------
    def _build(self, draft: _Draft) -> IntermediateRepresentation:
        reqs_by_scene: dict[str, list[RequirementSpec]] = {k: [] for k in draft.scenes}
        for pending in draft.requirements:
            for key in (pending.entity_key, pending.related_entity_key):
                if key is not None and key not in draft.entities:
                    raise StructuringError(
                        f"정의되지 않은 객체 '{key}' 를 참조합니다 (줄 {pending.line_no})"
                    )
            if pending.scene_keys:
                targets = pending.scene_keys
                for scene_key in targets:
                    if scene_key not in draft.scenes:
                        raise StructuringError(
                            f"정의되지 않은 장면 '{scene_key}' 을 참조합니다 (줄 {pending.line_no})"
                        )
                    if pending.entity_key is not None:
                        self._link(draft, scene_key, pending.entity_key, pending.line_no)
                    if pending.related_entity_key:
                        self._link(draft, scene_key, pending.related_entity_key, pending.line_no)
            elif pending.entity_key is None:
                targets = tuple(draft.scenes)  # 장면 수준 조건: 모든 장면
            else:
                targets = tuple(
                    k for k, members in draft.members.items() if pending.entity_key in members
                )
                if pending.related_entity_key:
                    targets = tuple(
                        k for k in targets if pending.related_entity_key in draft.members[k]
                    )
                if not targets:
                    raise StructuringError(
                        f"객체 '{pending.entity_key}' 가 등장하는 장면이 없어 조건을 둘 수 없습니다 "
                        f"('in <scene>: {pending.entity_key}' 로 연결하세요, 줄 {pending.line_no})"
                    )
            for scene_key in targets:
                reqs_by_scene[scene_key].append(
                    RequirementSpec(
                        scene_key=scene_key,
                        predicate=pending.predicate,
                        expected_value=pending.value,
                        entity_key=pending.entity_key,
                        related_entity_key=pending.related_entity_key,
                    )
                )
        scenes = tuple(
            SceneSpec(
                scene_key=s.scene_key,
                planned_start_ms=s.planned_start_ms,
                planned_end_ms=s.planned_end_ms,
                description=s.description,
                entity_keys=tuple(draft.members.get(s.scene_key, [])),
                requirements=tuple(reqs_by_scene[s.scene_key]),
            )
            for s in draft.scenes.values()
        )
        return IntermediateRepresentation(entities=tuple(draft.entities.values()), scenes=scenes)

    # ---- helpers -------------------------------------------------------------
    @staticmethod
    def _split(csv: str) -> list[str]:
        return [item.strip() for item in csv.split(",") if item.strip()]

    @classmethod
    def _scene_list(cls, scenes: str | None) -> tuple[str, ...]:
        return tuple(cls._split(scenes)) if scenes else ()

    @staticmethod
    def _link(draft: _Draft, scene_key: str, entity_key: str, line_no: int) -> None:
        if scene_key not in draft.scenes:
            raise StructuringError(f"정의되지 않은 장면 '{scene_key}' 을 참조합니다 (줄 {line_no})")
        if entity_key not in draft.entities:
            raise StructuringError(
                f"정의되지 않은 객체 '{entity_key}' 를 참조합니다 (줄 {line_no})"
            )
        members = draft.members.setdefault(scene_key, [])
        if entity_key not in members:
            members.append(entity_key)
