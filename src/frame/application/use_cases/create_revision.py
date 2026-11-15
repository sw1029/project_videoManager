"""US-01 수정 버전 생성: 원본 조건을 복제(Prototype)하고 선택 속성만 바꾼 새 DRAFT 를 만든다. 원본은 보존된다.

수정의 종류 (ADR-0017)
- CHANGE: 요구조건의 값을 바꾼다. 조건이 바뀐 장면만 다시 만들어진다.
- REPAIR: 요구조건은 그대로 두고, 그 요구조건이 걸린 장면을 다시 만든다 (영상이 요구와 어긋났을 때).
  조건이 같으면 부모 클립이 재사용되므로, 대상 장면의 seed 를 다시 지정해 새로 만들어지게 한다.
어느 쪽이든 '무엇을 하려는 수정인지' 와 '그대로 둘 요소' 를 수정 버전에 남긴다 (생성 지시문·재검수·화면이 쓴다).
"""

from __future__ import annotations

from dataclasses import replace

from frame.domain.errors import NotFound
from frame.domain.revision import ChangePropagationPolicy
from frame.domain.revision.resolution import resolve_revision, revision_draft

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
            ir = source.ensure_revisable()

            change = command.change
            resolved = resolve_revision(
                ir, change, self._policy, kind=command.kind, keep=command.keep
            )
            revision = revision_draft(
                source,
                resolved,
                change,
                version_id=self._ids.new_id("ver"),
                created_at=self._clock.now(),
                request_text=command.request_text,
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
            changed_scene_keys=resolved.target_scene_keys,
            kind=resolved.kind,
        )
