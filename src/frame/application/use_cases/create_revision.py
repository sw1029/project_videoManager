"""US-01 수정 버전 생성: 원본 조건을 복제(Prototype)하고 선택 속성만 바꾼 새 DRAFT 를 만든다. 원본은 보존된다."""

from __future__ import annotations

from dataclasses import replace

from frame.domain.enums import VersionState
from frame.domain.errors import NotFound, ValidationError, VersionNotRevisable
from frame.domain.models import VideoVersion
from frame.domain.services.propagation import ChangePropagationPolicy
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

            propagated = self._policy.apply(source.ir, command.change)
            if not propagated.changed_scene_keys:
                raise ValidationError("변경 사항이 없습니다 (이미 같은 값입니다)")

            revision = VideoVersion(
                version_id=self._ids.new_id("ver"),
                project_id=source.project_id,
                state=VersionState.DRAFT,
                created_at=self._clock.now(),
                parent_version_id=source.version_id,
                source_prompt=source.source_prompt,
                target_duration_ms=source.target_duration_ms,
                seed=source.seed,  # 같은 seed -> 바뀌지 않은 장면은 같은 결과를 기대할 수 있다
                ir=propagated.ir,
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
            changed_scene_keys=propagated.changed_scene_keys,
        )
