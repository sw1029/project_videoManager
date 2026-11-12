"""수정 요청 하나를 '무엇을 하는 수정인가' 로 풀어낸다 (ADR-0017, 0019).

수정 버전을 만들 때(CreateRevision)와 계획을 미리 보여 줄 때(PlanRevision)가 같은 규칙을 쓴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..enums import RevisionKind
from ..errors import ValidationError
from ..ir import IntermediateRepresentation
from ..revision import KeepItem, normalize_keep, repair_targets
from .propagation import AttributeChange, ChangePropagationPolicy


@dataclass(frozen=True)
class ResolvedRevision:
    kind: RevisionKind
    ir: IntermediateRepresentation  # 수정 버전의 중간표현 (REPAIR 면 원래 것 그대로)
    target_scene_keys: tuple[str, ...]  # 다시 만들 장면
    previous_value: str | None  # CHANGE: 대상 장면의 이전 값 (장면마다 다르거나 없던 조건이면 None)
    keep: tuple[KeepItem, ...]


def resolve_revision(
    ir: IntermediateRepresentation,
    change: AttributeChange,
    policy: ChangePropagationPolicy,
    *,
    kind: RevisionKind | None = None,
    keep: Sequence[KeepItem] = (),
) -> ResolvedRevision:
    """kind 를 주지 않으면 스스로 정한다: 값이 달라지면 CHANGE, 이미 그 값이면 REPAIR.

    - CHANGE 인데 달라지는 것이 없으면 ValidationError (REPAIR 를 안내한다).
    - REPAIR 인데 값이 요구조건과 다르거나 그 요구조건이 없으면 ValidationError.
    """
    kept = normalize_keep(ir, keep, change)
    if kind is RevisionKind.REPAIR:
        return ResolvedRevision(RevisionKind.REPAIR, ir, repair_targets(ir, change), None, kept)
    propagated = policy.apply(ir, change)
    if propagated.changed_scene_keys:
        targets = propagated.changed_scene_keys
        return ResolvedRevision(
            RevisionKind.CHANGE, propagated.ir, targets, _previous_value(ir, change, targets), kept
        )
    if kind is RevisionKind.CHANGE:
        raise ValidationError(
            "변경 사항이 없습니다 (이미 같은 값입니다). 요구조건은 그대로 두고 "
            "그 장면을 다시 만들려면 kind=REPAIR 로 요청하세요"
        )
    return ResolvedRevision(RevisionKind.REPAIR, ir, repair_targets(ir, change), None, kept)


def _previous_value(
    ir: IntermediateRepresentation, change: AttributeChange, scene_keys: Sequence[str]
) -> str | None:
    values = {
        r.expected_value
        for key in scene_keys
        for r in ir.scene(key).requirements
        if r.entity_key == change.entity_key and r.predicate == change.predicate
    }
    return next(iter(values)) if len(values) == 1 else None
