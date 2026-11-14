"""규칙 기반 변경 요청 해석 (FRAME_CHANGE_INTERPRETER=rule_based, ADR-0019). LLM 없이 도는 DSL 이다.

형식 (줄바꿈이나 ; 로 나눈다)
    cup.color = teal                    객체 속성. 값 뒤에 '@ 장면, 장면' 을 붙이면 그 장면만
    scene.lighting = cold               장면 수준 조건
    barista holds -> tray               관계 (상대 객체)
    keep: barista.action, cup.shape, lighting, background
유지할 요소는 '객체.요소' 또는 장면 요소 이름으로 쓴다 (영어 이름과 한국어 별칭을 읽는다).

읽은 객체와 유지 요소는 LLM 해석기와 같은 규칙으로 이 영상에 맞춘다 (_reading): 바꿀 객체가 이 영상에 없으면
ChangeRequestNotUnderstood, 쓸 수 없는 유지 요소는 빼고 notes 에 적는다.
"""

from __future__ import annotations

import re

from frame.application.ports.change_request import (
    ChangeRequestInterpretation,
    ChangeRequestInterpreter,
    ChangeRequestNotUnderstood,
)
from frame.domain.enums import ChangeScope, KeepAspect
from frame.domain.errors import ValidationError
from frame.domain.ir import IntermediateRepresentation
from frame.domain.revision import KeepItem

from ._reading import known_entity, usable_keep, with_left_out

_ATTRIBUTE = re.compile(r"^\s*([\w-]+)\s*\.\s*([\w-]+)\s*=\s*(.+?)\s*$")
_RELATION = re.compile(r"^\s*([\w-]+)\s+([\w-]+)\s*->\s*([\w-]+)\s*$")
_KEEP = re.compile(r"^\s*(?:keep|유지)\s*[:：]\s*(.+)$", re.IGNORECASE)
_ASPECTS = {
    "appearance": KeepAspect.APPEARANCE,
    "모습": KeepAspect.APPEARANCE,
    "외형": KeepAspect.APPEARANCE,
    "action": KeepAspect.ACTION,
    "동작": KeepAspect.ACTION,
    "자세": KeepAspect.ACTION,
    "shape": KeepAspect.SHAPE,
    "형태": KeepAspect.SHAPE,
    "크기": KeepAspect.SHAPE,
    "background": KeepAspect.BACKGROUND,
    "배경": KeepAspect.BACKGROUND,
    "lighting": KeepAspect.LIGHTING,
    "조명": KeepAspect.LIGHTING,
    "composition": KeepAspect.COMPOSITION,
    "구도": KeepAspect.COMPOSITION,
    "카메라": KeepAspect.COMPOSITION,
}
_HINT = "규칙 기반 해석은 'cup.color = teal @ 장면' 이나 'barista holds -> tray' 형식만 읽습니다"


class RuleBasedChangeRequestInterpreter(ChangeRequestInterpreter):
    def interpret(
        self, ir: IntermediateRepresentation, text: str, *, entity_key: str | None = None
    ) -> ChangeRequestInterpretation:
        clauses = [c.strip() for c in re.split(r"[;\n]+", text) if c.strip()]
        change: tuple[str | None, str, str, tuple[str, ...]] | None = None
        keep: list[KeepItem] = []
        for clause in clauses:
            kept = _KEEP.match(clause)
            if kept:
                keep.extend(_keep_items(kept.group(1)))
                continue
            if change is not None:
                raise ChangeRequestNotUnderstood(
                    f"변경은 한 번에 하나만 요청할 수 있습니다: {clause}"
                )
            change = _change(clause)
        if change is None:
            raise ChangeRequestNotUnderstood(f"변경 요청을 읽지 못했습니다. {_HINT}")
        entity, predicate, value, scenes = change
        if entity is not None:
            entity = known_entity(ir, entity)
        usable, missing, clashing = usable_keep(
            ir, keep, entity_key=entity, predicate=predicate, new_value=value
        )
        return ChangeRequestInterpretation(
            entity_key=entity,
            predicate=predicate,
            new_value=value,
            scope=ChangeScope.SCENES if scenes else None,
            scene_keys=scenes,
            keep=usable,
            notes=with_left_out("규칙 기반 형식으로 읽었습니다", missing, clashing),
        )


def _change(clause: str) -> tuple[str | None, str, str, tuple[str, ...]]:
    body, _, where = clause.partition("@")
    scenes = tuple(s.strip() for s in where.split(",") if s.strip())
    relation = _RELATION.match(body)
    if relation:
        entity, predicate, other = relation.groups()
        return entity, predicate, other, scenes
    attribute = _ATTRIBUTE.match(body)
    if attribute:
        entity, predicate, value = attribute.groups()
        return (None if entity.lower() == "scene" else entity), predicate, value, scenes
    raise ChangeRequestNotUnderstood(f"변경 요청을 읽지 못했습니다: '{clause}'. {_HINT}")


def _keep_items(text: str) -> list[KeepItem]:
    items = []
    for token in (t.strip() for t in text.split(",")):
        if not token:
            continue
        entity, _, name = token.rpartition(".")
        aspect = _ASPECTS.get(name.strip().lower())
        if aspect is None:
            raise ChangeRequestNotUnderstood(
                f"유지할 요소를 읽지 못했습니다: '{token}' (가능한 값: {', '.join(sorted(_ASPECTS))})"
            )
        try:
            items.append(KeepItem(aspect, entity.strip() or None))
        except ValidationError as exc:
            raise ChangeRequestNotUnderstood(f"{token}: {exc.message}") from exc
    return items
