"""속성·관계 변경 전파 정책 (US-01 AC-02: 연관 장면 자동 전파).

Strategy 패턴: 전파 규칙을 바꾸고 싶으면 ChangePropagationPolicy 를 새로 구현해 주입한다.

규칙
- 속성 술어: 대상 장면의 (객체, 술어) 조건 값을 교체하거나 없으면 추가한다. 값은 어휘 규칙으로 정규화된다.
- 관계 술어: 상대 객체를 바꾼다. 새 상대 객체는 그 장면에 연결되어 있어야 하며, 값과 상대 객체 키를 함께 바꾼다.
- 같은 (객체, 술어) 조건이 여러 개(관계)면 current_value 로 바꿀 대상을 지정해야 한다.
- entity_key=None 은 장면 수준 조건(장소·조명·분위기·카메라)이다. 모든 장면 또는 지정 장면에 적용하며
  관계 술어는 쓸 수 없다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

from ..enums import ChangeScope, EntityKind
from ..errors import NotFound, ValidationError
from ..ir import IntermediateRepresentation, RequirementSpec, SceneSpec
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


@dataclass(frozen=True)
class PropagationResult:
    ir: IntermediateRepresentation
    changed_scene_keys: tuple[str, ...]


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


class ChangePropagationPolicy(ABC):
    @abstractmethod
    def apply(self, ir: IntermediateRepresentation, change: AttributeChange) -> PropagationResult:
        """원본 IR 을 변경하지 않고, 변경이 반영된 새 IR 과 바뀐 장면 목록을 돌려준다."""


class DefaultPropagationPolicy(ChangePropagationPolicy):
    def apply(self, ir: IntermediateRepresentation, change: AttributeChange) -> PropagationResult:
        if change.entity_key is None:
            if DEFAULT_VOCABULARY.is_relational(change.predicate) is True:
                raise ValidationError(
                    f"'{change.predicate}' 는 관계 술어라 장면 수준 조건으로 바꿀 수 없습니다 "
                    "(entity_key 를 지정하세요)"
                )
        targets = target_scenes(ir, change)

        new_scenes: list[SceneSpec] = []
        changed: list[str] = []
        for scene in ir.scenes:
            if scene.scene_key not in targets:
                new_scenes.append(scene)
                continue
            reqs = list(scene.requirements)
            matches = [
                i
                for i, r in enumerate(reqs)
                if r.entity_key == change.entity_key
                and r.predicate == change.predicate
                and (change.current_value is None or r.expected_value == change.current_value)
            ]
            if len(matches) > 1:
                raise ValidationError(
                    f"장면 '{scene.scene_key}' 에 '{change.predicate}' 조건이 여러 개입니다. "
                    "current_value 로 바꿀 대상을 지정하세요"
                )
            if not matches and change.current_value is not None:
                new_scenes.append(scene)  # 지정한 현재 값이 없는 장면은 건드리지 않는다
                continue
            existing = reqs[matches[0]] if matches else None
            replacement = self._build_requirement(ir, scene, change, existing)
            if existing is not None and existing == replacement:
                new_scenes.append(scene)  # 이미 같은 값: 변경 없음
                continue
            if existing is None:
                reqs.append(replacement)
            else:
                reqs[matches[0]] = replacement
            new_scenes.append(replace(scene, requirements=tuple(reqs)))
            changed.append(scene.scene_key)

        return PropagationResult(
            ir=replace(ir, scenes=tuple(new_scenes)), changed_scene_keys=tuple(changed)
        )

    @staticmethod
    def _build_requirement(
        ir: IntermediateRepresentation,
        scene: SceneSpec,
        change: AttributeChange,
        existing: RequirementSpec | None,
    ) -> RequirementSpec:
        relational = (
            existing.is_relational
            if existing is not None
            else DEFAULT_VOCABULARY.is_relational(change.predicate) is True
        )
        if not relational:
            return RequirementSpec(
                scene.scene_key, change.predicate, change.new_value, change.entity_key
            )
        if change.new_value == change.entity_key:
            raise ValidationError("자기 자신과의 관계는 만들 수 없습니다")
        if (
            ir.has_entity(change.new_value)
            and ir.entity(change.new_value).kind is EntityKind.BACKGROUND
        ):
            raise ValidationError(
                f"배경 객체 '{change.new_value}' 는 관계 상대가 될 수 없습니다 (장소는 location 조건으로 표현)"
            )
        if change.new_value not in scene.entity_keys:
            raise ValidationError(
                f"관계 상대 객체 '{change.new_value}' 는 장면 '{scene.scene_key}' 에 "
                "연결되어 있어야 합니다"
            )
        return RequirementSpec(
            scene.scene_key,
            change.predicate,
            change.new_value,
            change.entity_key,
            related_entity_key=change.new_value,
        )
