"""해석기들이 함께 쓰는 규칙: 읽어 낸 객체·장면·유지 요소를 이 영상(중간표현)의 것에 맞춘다 (ADR-0019).

포트 계약은 "성공한 해석은 이 영상에 있는 객체만 가리킨다" 이다. 구현마다 따로 맞추면 같은 요청이 구현에 따라
다른 오류가 되고(없는 객체: 404 / 422), 유지 요소 하나 때문에 계획 전체가 거절된다 (감사 A-30).

- 키는 정확히 같지 않아도 맞춘다: 모델은 키 대신 질의의 한 줄을 돌려줄 때가 있다 ('cup (prop): 세라믹 머그컵').
- 바꿀 객체가 이 영상에 없으면 ChangeRequestNotUnderstood.
- 유지 요소는 쓸 수 있는 것만 남긴다: 이 영상에 없는 객체의 것과, 이 변경이 바꾸는 것 자체를 그대로 두라는 것은
  뺀다. 뺀 것은 notes 에 적어 화면에 보이게 한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from frame.adapters.shared.vlm.answers import requested_key
from frame.application.ports.change_request import ChangeRequestNotUnderstood
from frame.domain.errors import NotFound, ValidationError
from frame.domain.ir import IntermediateRepresentation
from frame.domain.revision import AttributeChange, KeepItem, normalize_keep


def known_entity(ir: IntermediateRepresentation, raw: str, notes: str = "") -> str:
    """읽어 낸 객체 키를 이 영상의 객체 키로 맞춘다. 맞는 객체가 없으면 ChangeRequestNotUnderstood."""
    key = entity_in(ir, raw)
    if key is None:
        raise ChangeRequestNotUnderstood(
            f"요청이 가리키는 객체 '{raw}' 가 이 영상에 없습니다" + (f" ({notes})" if notes else "")
        )
    return key


def entity_in(ir: IntermediateRepresentation, raw: str) -> str | None:
    return requested_key(raw, [entity.entity_key for entity in ir.entities])


def known_scenes(
    ir: IntermediateRepresentation, raws: Iterable[object]
) -> tuple[tuple[str, ...], list[str]]:
    """(이 영상에 있는 장면 키, 없어서 뺀 것의 설명)."""
    wanted = [scene.scene_key for scene in ir.scenes]
    scenes: list[str] = []
    missing: list[str] = []
    for raw in raws:
        key = requested_key(str(raw), wanted)
        if key is None:
            missing.append(f"장면 {raw}")
        elif key not in scenes:
            scenes.append(key)
    return tuple(scenes), missing


def usable_keep(
    ir: IntermediateRepresentation,
    items: Sequence[KeepItem],
    *,
    entity_key: str | None,
    predicate: str,
    new_value: str,
) -> tuple[tuple[KeepItem, ...], list[str], list[str]]:
    """(쓸 수 있는 유지 요소, 이 영상에 없어 뺀 것, 이 변경이 바꾸는 것 자체라 뺀 것).

    쓸 수 있는지는 수정 요청을 풀 때와 같은 도메인 규칙(normalize_keep)으로 본다.
    """
    change = AttributeChange(entity_key, predicate, new_value)
    kept: list[KeepItem] = []
    missing: list[str] = []
    clashing: list[str] = []
    for item in items:
        try:
            normalize_keep(ir, (item,), change)
        except NotFound:
            missing.append(f"유지 요소의 객체 {item.entity_key}")
        except ValidationError:
            clashing.append(_describe(item))
        else:
            if item not in kept:
                kept.append(item)
    return tuple(kept), missing, clashing


def with_left_out(notes: str, missing: Sequence[str], clashing: Sequence[str]) -> str:
    """해석 메모 뒤에 뺀 것을 덧붙인다 (화면에 보여 준다)."""
    parts = [notes] if notes else []
    if missing:
        parts.append(f"(이 영상에 없어 뺀 것: {', '.join(missing)})")
    if clashing:
        parts.append(f"(바꾸는 것 자체라 뺀 유지 요소: {', '.join(clashing)})")
    return " ".join(parts)


def _describe(item: KeepItem) -> str:
    return f"{item.entity_key} 의 {item.aspect}" if item.entity_key else f"장면의 {item.aspect}"
