"""속성 변경과 그 대상 장면.

- AttributeChange: 수정 요청 하나 - 어느 객체(또는 장면)의 어느 술어를 어떤 값으로. 값은 어휘 규칙으로 정규화된다.
  entity_key=None 은 장면 수준 조건(장소·조명·분위기·카메라)이다.
- target_scenes: 그 변경의 범위(scope)에 드는 장면.
- repair_targets: 다시 맞추기(REPAIR)로 다시 만들 장면 - 범위 안에서 그 요구조건이 그 값으로 걸려 있는 장면.
- next_seed: 다시 만드는 장면에 쓸 seed.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..enums import ChangeScope
from ..errors import NotFound, ValidationError
from ..ir import IntermediateRepresentation
from ..vocabulary import DEFAULT_VOCABULARY


@dataclass(frozen=True)
class AttributeChange:
    entity_key: str | None  # None = 장면 수준 조건
    predicate: str
    new_value: str  # 속성이면 새 값, 관계면 새 상대 객체 키
    scope: ChangeScope = ChangeScope.ALL_APPEARANCES
    scene_keys: tuple[str, ...] = ()  # scope=SCENES 일 때 대상 장면
    current_value: str | None = None  # 같은 술어의 조건이 여러 개일 때 바꿀 대상의 현재 값

    def __post_init__(self) -> None:
        predicate = DEFAULT_VOCABULARY.normalize(self.predicate)
        object.__setattr__(self, "predicate", predicate)
        value = DEFAULT_VOCABULARY.normalize_value(predicate, self.new_value or "")
        if not value:
            raise ValidationError("새 값(new_value)이 비어 있습니다")
        object.__setattr__(self, "new_value", value)
        if self.current_value is not None:
            object.__setattr__(
                self,
                "current_value",
                DEFAULT_VOCABULARY.normalize_value(predicate, self.current_value),
            )


def target_scenes(ir: IntermediateRepresentation, change: AttributeChange) -> list[str]:
    """변경(또는 REPAIR)의 범위에 드는 장면 키. 객체가 등장하는 장면 안에서 scope 로 고른다."""
    if change.entity_key is None:
        appearances = {s.scene_key for s in ir.scenes}
    else:
        if not ir.has_entity(change.entity_key):
            raise NotFound(f"객체 '{change.entity_key}' 가 없습니다", entity_key=change.entity_key)
        appearances = {s.scene_key for s in ir.scenes_with_entity(change.entity_key)}
    if change.scope is ChangeScope.SCENES:
        if not change.scene_keys:
            raise ValidationError("scope=SCENES 이면 적용할 장면을 하나 이상 지정해야 합니다")
        unknown = [k for k in change.scene_keys if not ir.has_scene(k)]
        if unknown:
            raise NotFound(f"존재하지 않는 장면: {unknown}", scene_keys=tuple(unknown))
        absent = [k for k in change.scene_keys if k not in appearances]
        if absent:
            raise ValidationError(
                f"객체 '{change.entity_key}' 가 등장하지 않는 장면에는 적용할 수 없습니다: {absent}"
            )
        return list(change.scene_keys)
    targets = [s.scene_key for s in ir.scenes if s.scene_key in appearances]
    if not targets:
        raise ValidationError(f"객체 '{change.entity_key}' 가 등장하는 장면이 없습니다")
    return targets


def repair_targets(ir: IntermediateRepresentation, change: AttributeChange) -> tuple[str, ...]:
    """REPAIR 로 다시 만들 장면: 범위 안에서 요구조건 (객체, 술어) 이 그 값으로 걸려 있는 장면.

    - 값이 다르면 ValidationError (값을 바꾸는 것은 CHANGE 다).
    - scope=SCENES 로 짚은 장면에 그 요구조건이 없으면 ValidationError.
      모든 등장 장면(ALL_APPEARANCES)이면 요구조건이 없는 장면은 대상에서 뺀다.
    """
    targets: list[str] = []
    for scene_key in target_scenes(ir, change):
        scene = ir.scene(scene_key)
        found = [
            r
            for r in scene.requirements
            if r.entity_key == change.entity_key and r.predicate == change.predicate
        ]
        if any(r.expected_value == change.new_value for r in found):
            targets.append(scene_key)
            continue
        if found:
            values = ", ".join(sorted({r.expected_value for r in found}))
            raise ValidationError(
                f"장면 '{scene_key}' 의 '{change.predicate}' 요구조건은 {values} 입니다. "
                "REPAIR 는 요구조건을 바꾸지 않습니다 (값을 바꾸려면 kind=CHANGE)"
            )
        if change.scope is ChangeScope.SCENES:
            raise ValidationError(
                f"장면 '{scene_key}' 에는 '{change.predicate}' 요구조건이 없습니다 (다시 맞출 대상이 없음)"
            )
    if not targets:
        raise ValidationError(
            f"'{change.predicate}' = {change.new_value} 요구조건이 걸린 장면이 없습니다"
        )
    return tuple(targets)


def next_seed(seed: int | None) -> int:
    """다시 만드는 장면에 쓸 seed. 지금 것과 달라야 같은 요청의 캐시된 결과가 그대로 나오지 않는다."""
    return (seed or 0) + 1
