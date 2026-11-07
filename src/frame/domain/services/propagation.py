"""속성 변경 전파 정책 (US-01 AC-02: 연관 장면 자동 전파).

Strategy 패턴: 전파 규칙을 바꾸고 싶으면 ChangePropagationPolicy 를 새로 구현해 주입한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

from ..enums import ChangeScope
from ..errors import NotFound, ValidationError
from ..ir import IntermediateRepresentation, RequirementSpec


@dataclass(frozen=True)
class AttributeChange:
    entity_key: str
    predicate: str
    new_value: str
    scope: ChangeScope = ChangeScope.ALL_APPEARANCES
    scene_keys: tuple[str, ...] = ()  # scope=SCENES 일 때 대상 장면


@dataclass(frozen=True)
class PropagationResult:
    ir: IntermediateRepresentation
    changed_scene_keys: tuple[str, ...]


class ChangePropagationPolicy(ABC):
    @abstractmethod
    def apply(self, ir: IntermediateRepresentation, change: AttributeChange) -> PropagationResult:
        """원본 IR 을 변경하지 않고, 변경이 반영된 새 IR 과 바뀐 장면 목록을 돌려준다."""


class DefaultPropagationPolicy(ChangePropagationPolicy):
    """대상 장면의 (entity, predicate) 요구조건을 교체하거나 없으면 추가한다."""

    def apply(self, ir: IntermediateRepresentation, change: AttributeChange) -> PropagationResult:
        if not ir.has_entity(change.entity_key):
            raise NotFound(f"객체 '{change.entity_key}' 가 없습니다", entity_key=change.entity_key)
        appearances = {s.scene_key for s in ir.scenes_with_entity(change.entity_key)}
        targets = self._resolve_targets(ir, change, appearances)

        new_scenes = []
        changed: list[str] = []
        for scene in ir.scenes:
            if scene.scene_key not in targets:
                new_scenes.append(scene)
                continue
            reqs = list(scene.requirements)
            idx = next(
                (
                    i
                    for i, r in enumerate(reqs)
                    if r.entity_key == change.entity_key and r.predicate == change.predicate
                ),
                None,
            )
            if idx is None:
                reqs.append(
                    RequirementSpec(
                        scene_key=scene.scene_key,
                        predicate=change.predicate,
                        expected_value=change.new_value,
                        entity_key=change.entity_key,
                    )
                )
            elif reqs[idx].expected_value == change.new_value:
                new_scenes.append(scene)  # 이미 같은 값: 변경 없음
                continue
            else:
                reqs[idx] = replace(reqs[idx], expected_value=change.new_value)
            new_scenes.append(replace(scene, requirements=tuple(reqs)))
            changed.append(scene.scene_key)

        return PropagationResult(
            ir=replace(ir, scenes=tuple(new_scenes)), changed_scene_keys=tuple(changed)
        )

    @staticmethod
    def _resolve_targets(
        ir: IntermediateRepresentation, change: AttributeChange, appearances: set[str]
    ) -> list[str]:
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
