"""US-01 수정 버전 생성: 원본 조건을 복제(Prototype)하고 선택 속성만 바꾼 새 DRAFT 를 만든다. 원본은 보존된다.

수정의 종류 (ADR-0017)
- CHANGE: 요구조건의 값을 바꾼다. 조건이 바뀐 장면만 다시 만들어진다.
- REPAIR: 요구조건은 그대로 두고, 그 요구조건이 걸린 장면을 다시 만든다 (영상이 요구와 어긋났을 때).
  조건이 같으면 부모 클립이 재사용되므로, 대상 장면의 seed 를 다시 지정해 새로 만들어지게 한다.
어느 쪽이든 '무엇을 하려는 수정인지' 와 '그대로 둘 요소' 를 수정 버전에 남긴다 (생성 지시문·재검수·화면이 쓴다).
"""

from __future__ import annotations

from dataclasses import replace

from frame.domain.enums import RevisionKind, VersionState
from frame.domain.errors import NotFound, ValidationError, VersionNotRevisable
from frame.domain.ir import IntermediateRepresentation
from frame.domain.models import VideoVersion
from frame.domain.revision import RevisionIntent, next_seed, normalize_keep, repair_targets
from frame.domain.services.propagation import AttributeChange, ChangePropagationPolicy
from frame.domain.state_machine import REVISABLE_STATES

from ..dto import CreateRevisionCommand, CreateRevisionResult
from ..ports.common import Clock, IdGenerator
from ..ports.unit_of_work import UnitOfWorkFactory


class CreateRevision:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        policy: ChangePropagationPolicy,
    ) -> None:
        self._uow_factory = uow_factory
        self._ids = ids
        self._clock = clock
        self._policy = policy

    def execute(self, command: CreateRevisionCommand) -> CreateRevisionResult:
        with self._uow_factory() as uow:
            source = uow.versions.get(command.version_id)
            if source is None:
                raise NotFound(f"버전 {command.version_id} 을 찾을 수 없습니다")
            if source.state not in REVISABLE_STATES or source.ir is None:
                raise VersionNotRevisable(
                    f"상태 {source.state} 인 버전은 수정할 수 없습니다", state=source.state
                )

            change = command.change
            keep = normalize_keep(source.ir, command.keep, change)
            scene_seeds = dict(source.scene_seeds)  # 물려받는다 (다시 만든 장면이 계속 재사용되게)
            previous_value: str | None = None
            if command.kind is RevisionKind.REPAIR:
                ir = source.ir
                targets = repair_targets(ir, change)
                for scene_key in targets:
                    scene_seeds[scene_key] = next_seed(source.seed_for(scene_key))
            else:
                propagated = self._policy.apply(source.ir, change)
                if not propagated.changed_scene_keys:
                    raise ValidationError(
                        "변경 사항이 없습니다 (이미 같은 값입니다). 요구조건은 그대로 두고 "
                        "그 장면을 다시 만들려면 kind=REPAIR 로 요청하세요"
                    )
                ir, targets = propagated.ir, propagated.changed_scene_keys
                previous_value = _previous_value(source.ir, change, targets)
            text = (command.request_text or "").strip()
            intent = RevisionIntent(
                kind=command.kind,
                predicate=change.predicate,
                value=change.new_value,
                entity_key=change.entity_key,
                scope=change.scope,
                target_scene_keys=tuple(targets),
                previous_value=previous_value,
                keep=keep,
                request_text=text or None,
            )

            revision = VideoVersion(
                version_id=self._ids.new_id("ver"),
                project_id=source.project_id,
                state=VersionState.DRAFT,
                created_at=self._clock.now(),
                parent_version_id=source.version_id,
                source_prompt=source.source_prompt,
                target_duration_ms=source.target_duration_ms,
                seed=source.seed,  # 같은 seed -> 바뀌지 않은 장면은 같은 결과를 기대할 수 있다
                ir=ir,
                scene_seeds=scene_seeds,
                intent=intent,
            )
            uow.versions.add(revision)
            # 참조 이미지를 물려준다. 정체성 기준 이미지(ADR-0015)는 출처·기준 장면·외형 해시까지 그대로 옮겨야
            # 수정 버전에서 유효성을 다시 판단할 수 있다 (사용자 참조로 바뀌면 장면 조건이 달라져 재사용이 깨진다)
            for ref in uow.reference_images.list_by_version(source.version_id):
                uow.reference_images.add(
                    replace(
                        ref,
                        reference_image_id=self._ids.new_id("img"),
                        version_id=revision.version_id,
                    )
                )
            uow.commit()

        return CreateRevisionResult(
            version_id=revision.version_id,
            parent_version_id=source.version_id,
            state=revision.state,
            changed_scene_keys=tuple(targets),
            kind=command.kind,
        )


def _previous_value(
    ir: IntermediateRepresentation, change: AttributeChange, scene_keys: tuple[str, ...]
) -> str | None:
    """바뀌는 장면들에서 그 조건의 이전 값. 장면마다 다르거나 없던 조건이면 None."""
    values = {
        r.expected_value
        for key in scene_keys
        for r in ir.scene(key).requirements
        if r.entity_key == change.entity_key and r.predicate == change.predicate
    }
    return next(iter(values)) if len(values) == 1 else None
