"""수정 의도 (ADR-0017): 수정 버전이 무엇을 하려는지와, 그대로 두라고 한 요소.

- 종류
  - CHANGE: 요구조건의 값을 바꾼다 (US-01 의 선택 수정).
  - REPAIR: 요구조건은 그대로 두고, 그 요구조건이 반영되지 않은 장면을 다시 만든다.
    컨셉 화면의 흐름이다: 원문은 '청록색 컵' 인데 영상에는 빨간 컵 -> 그 구간만 재생성.
- 유지할 요소(KeepItem): 다시 만드는 장면에서 그대로 두라고 요청한 것. 생성기의 지시문과 재검수가 쓴다.
- 의도는 수정 버전에 저장된다. 조건(IR)이 아니므로 fingerprint 에는 들어가지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..enums import ChangeScope, KeepAspect, RevisionKind
from ..errors import NotFound, ValidationError
from ..ir import IntermediateRepresentation, SceneSpec
from ..vocabulary import DEFAULT_VOCABULARY
from .changes import AttributeChange

ENTITY_ASPECTS = frozenset({KeepAspect.APPEARANCE, KeepAspect.ACTION, KeepAspect.SHAPE})
SCENE_ASPECTS = frozenset({KeepAspect.BACKGROUND, KeepAspect.LIGHTING, KeepAspect.COMPOSITION})

# 그 술어를 바꾸면(또는 바로잡으면) 함께 달라지는 요소: 그것을 '그대로 두라' 고 할 수는 없다
_SHAPE_PREDICATES = frozenset({"shape", "size"})
_SCENE_PREDICATE_ASPECT = {
    "location": KeepAspect.BACKGROUND,
    "lighting": KeepAspect.LIGHTING,
    "time_of_day": KeepAspect.LIGHTING,
    "weather": KeepAspect.LIGHTING,
    "camera": KeepAspect.COMPOSITION,
}


@dataclass(frozen=True)
class KeepItem:
    """다시 만드는 장면에서 그대로 둘 요소 하나. 객체의 모습·동작·형태 또는 장면의 배경·조명·구도."""

    aspect: KeepAspect
    entity_key: str | None = None  # 객체 요소면 그 객체, 장면 요소면 None

    def __post_init__(self) -> None:
        try:
            aspect = KeepAspect(self.aspect)
        except ValueError as exc:
            raise ValidationError(f"알 수 없는 유지 요소: {self.aspect}") from exc
        object.__setattr__(self, "aspect", aspect)
        if aspect in ENTITY_ASPECTS and not self.entity_key:
            raise ValidationError(f"유지할 요소 {aspect} 에는 객체(entity_key)를 지정해야 합니다")
        if aspect in SCENE_ASPECTS and self.entity_key is not None:
            raise ValidationError(
                f"유지할 요소 {aspect} 는 장면 전체에 대한 것이라 객체를 지정할 수 없습니다"
            )

    @property
    def is_scene_level(self) -> bool:
        return self.entity_key is None

    def applies_to(self, scene: SceneSpec) -> bool:
        return self.entity_key is None or self.entity_key in scene.entity_keys

    def to_dict(self) -> dict[str, Any]:
        return {"aspect": str(self.aspect), "entity_key": self.entity_key}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> KeepItem:
        return cls(KeepAspect(data["aspect"]), data.get("entity_key"))


@dataclass(frozen=True)
class RevisionIntent:
    """수정 버전 하나가 하려는 것. 수정 버전에 저장돼 생성·재검수·화면이 함께 본다."""

    kind: RevisionKind
    predicate: str
    value: str  # CHANGE: 새 값 / REPAIR: 지켜져야 하는 값
    entity_key: str | None = None  # None = 장면 수준 조건
    scope: ChangeScope = ChangeScope.ALL_APPEARANCES
    target_scene_keys: tuple[str, ...] = ()  # 다시 만드는 장면
    previous_value: str | None = None  # CHANGE: 대상 장면의 이전 값 (장면마다 다르면 None)
    keep: tuple[KeepItem, ...] = ()
    request_text: str | None = None  # 사용자가 쓴 요청 원문 (표시·기록용. 생성에는 쓰지 않는다)

    def targets(self, scene_key: str) -> bool:
        return scene_key in self.target_scene_keys

    def keep_for(self, scene: SceneSpec) -> tuple[KeepItem, ...]:
        """그 장면에 해당하는 유지 요소 (그 장면에 나오는 객체의 것과 장면 수준의 것)."""
        return tuple(item for item in self.keep if item.applies_to(scene))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "entity_key": self.entity_key,
            "predicate": self.predicate,
            "value": self.value,
            "scope": str(self.scope),
            "target_scene_keys": list(self.target_scene_keys),
            "previous_value": self.previous_value,
            "keep": [item.to_dict() for item in self.keep],
            "request_text": self.request_text,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RevisionIntent:
        return cls(
            kind=RevisionKind(data["kind"]),
            predicate=data["predicate"],
            value=data["value"],
            entity_key=data.get("entity_key"),
            scope=ChangeScope(data.get("scope", ChangeScope.ALL_APPEARANCES)),
            target_scene_keys=tuple(data.get("target_scene_keys", ())),
            previous_value=data.get("previous_value"),
            keep=tuple(KeepItem.from_dict(item) for item in data.get("keep", ())),
            request_text=data.get("request_text"),
        )


def normalize_keep(
    ir: IntermediateRepresentation,
    keep: Sequence[KeepItem],
    change: AttributeChange,
) -> tuple[KeepItem, ...]:
    """유지할 요소를 검증하고 중복을 없앤다 (순서는 처음 나온 대로).

    - 없는 객체를 가리키면 NotFound.
    - 이 수정이 바꾸는(바로잡는) 것 자체를 그대로 두라고 하면 ValidationError.
      예: 컵 색을 바꾸면서 컵의 모습(APPEARANCE) 유지. 형태만 지키려면 SHAPE 를 쓴다.
    """
    altered = _altered_by(change.entity_key, change.predicate)
    seen: list[KeepItem] = []
    for item in keep:
        if item.entity_key is not None and not ir.has_entity(item.entity_key):
            raise NotFound(f"객체 '{item.entity_key}' 가 없습니다", entity_key=item.entity_key)
        if item == altered:
            raise ValidationError(
                f"'{change.predicate}' 을(를) 바꾸면서 {_describe(item)} 을(를) 유지하라고 할 수 없습니다"
            )
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def _altered_by(entity_key: str | None, predicate: str) -> KeepItem | None:
    """그 조건을 바꾸면 함께 달라지는 유지 요소 (없으면 None)."""
    if entity_key is None:
        aspect = _SCENE_PREDICATE_ASPECT.get(predicate)
        return KeepItem(aspect) if aspect is not None else None
    if DEFAULT_VOCABULARY.is_relational(predicate) is True:
        return None
    if predicate in _SHAPE_PREDICATES:
        return KeepItem(KeepAspect.SHAPE, entity_key)
    if DEFAULT_VOCABULARY.is_transient(predicate):
        return KeepItem(KeepAspect.ACTION, entity_key)
    return KeepItem(KeepAspect.APPEARANCE, entity_key)


def _describe(item: KeepItem) -> str:
    return f"{item.entity_key} 의 {item.aspect}" if item.entity_key else f"장면의 {item.aspect}"
