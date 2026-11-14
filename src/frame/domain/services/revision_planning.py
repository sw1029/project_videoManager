"""수정 요청 하나를 '무엇을 하는 수정인가' 로 풀어낸다 (ADR-0017, 0019).

수정 버전을 만들 때(CreateRevision)와 계획을 미리 보여 줄 때(PlanRevision)가 같은 규칙을 쓴다.
revision_draft 는 그 결과로 수정 버전을 만든다 (저장은 하지 않는다).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ..enums import RevisionKind, VersionState
from ..errors import ValidationError
from ..ir import IntermediateRepresentation
from ..models import VideoVersion
from ..revision import KeepItem, RevisionIntent, next_seed, normalize_keep, repair_targets
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
    - CHANGE 가 중간표현의 규칙(IntermediateRepresentation.validate)을 깨뜨리면 ValidationError.
    """
    kept = normalize_keep(ir, keep, change)
    if kind is RevisionKind.REPAIR:
        return ResolvedRevision(RevisionKind.REPAIR, ir, repair_targets(ir, change), None, kept)
    propagated = policy.apply(ir, change)
    if propagated.changed_scene_keys:
        _keep_the_rules(ir, propagated.ir)
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


def _keep_the_rules(before: IntermediateRepresentation, after: IntermediateRepresentation) -> None:
    """수정한 중간표현이 규칙을 지키는지 본다. 규칙은 IntermediateRepresentation.validate() 한 곳에 있다.

    구조화 결과는 그 검사를 거친다. 수정 전파의 결과도 거쳐야 구조화에서 거부되는 것이 수정으로 만들어지지 않는다
    (예: 배경 객체가 관계의 주체가 되는 수정). 수정 전부터 규칙에 어긋나 있던 중간표현(규칙이 생기기 전의 것)은
    이 수정이 만든 문제가 아니므로 막지 않는다.
    """
    try:
        after.validate()
    except ValidationError:
        try:
            before.validate()
        except ValidationError:
            return
        raise


def revision_draft(
    source: VideoVersion,
    resolved: ResolvedRevision,
    change: AttributeChange,
    *,
    version_id: str,
    created_at: datetime,
    request_text: str | None = None,
) -> VideoVersion:
    """수정 요청을 푼 결과로 수정 버전(DRAFT)을 만든다. 저장은 부르는 쪽이 한다.

    조건·장면별 seed·수정 의도가 여기서 정해진다. 수정 버전을 만들 때와 계획을 미리 볼 때가 같은 버전을 보므로,
    계획이 알려 준 것과 실제로 만들어지는 것이 어긋나지 않는다.
    - REPAIR 는 요구조건이 그대로라 부모 클립이 재사용된다. 대상 장면의 seed 를 다시 지정해 새로 만들어지게 한다.
    - 장면별 seed 는 물려받는다 (다시 만든 장면이 다음 수정 버전에서 재사용되게).
    """
    scene_seeds = dict(source.scene_seeds)
    if resolved.kind is RevisionKind.REPAIR:
        for scene_key in resolved.target_scene_keys:
            scene_seeds[scene_key] = next_seed(source.seed_for(scene_key))
    text = (request_text or "").strip()
    intent = RevisionIntent(
        kind=resolved.kind,
        predicate=change.predicate,
        value=change.new_value,
        entity_key=change.entity_key,
        scope=change.scope,
        target_scene_keys=resolved.target_scene_keys,
        previous_value=resolved.previous_value,
        keep=resolved.keep,
        request_text=text or None,
    )
    return VideoVersion(
        version_id=version_id,
        project_id=source.project_id,
        state=VersionState.DRAFT,
        created_at=created_at,
        parent_version_id=source.version_id,
        source_prompt=source.source_prompt,
        target_duration_ms=source.target_duration_ms,
        seed=source.seed,  # 같은 seed -> 바뀌지 않은 장면은 같은 결과를 기대할 수 있다
        ir=resolved.ir,
        scene_seeds=scene_seeds,
        intent=intent,
    )


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
