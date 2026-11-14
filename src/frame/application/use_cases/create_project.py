"""US-06 1단계: 요구사항·참조 이미지를 접수하고 초기 버전을 만든다.

입력 모드 (STRUCTURING_DESIGN §3)
- 자연어(또는 DSL) 요구사항: 버전을 STRUCTURING 으로 만들고 구조화 작업을 넘긴다 (비동기, 202).
- IR 직접 입력 (#10, 블록 입력 화면): LLM 없이 보정 -> 검증만 거쳐 바로 DRAFT 를 만든다 (동기, 201).
  요구사항 글을 함께 보내면 IR 을 우선하고 글은 원문(source_prompt)으로만 보관한다.
  고칠 수 없는 IR, IR 에 없는 객체를 가리키는 참조 이미지, 목표 길이와 맞지 않는 장면 합계는
  아무것도 저장하지 않고 거부한다 (422).
"""

from __future__ import annotations

from frame.domain.enums import VersionState
from frame.domain.errors import ValidationError
from frame.domain.models import Project, ReferenceImage, VideoVersion

from ..dto import CreateProjectCommand, CreateProjectResult
from ..jobs import STRUCTURE_REQUIREMENTS
from ..ports.common import Clock, IdGenerator
from ..ports.jobs import Job, JobDispatcher
from ..ports.structurer import IrDocumentStructurer, StructuredDocument
from ..ports.unit_of_work import UnitOfWorkFactory
from ..structuring_rules import check_reference_entities, check_target_duration


class CreateProject:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        dispatcher: JobDispatcher,
        ids: IdGenerator,
        clock: Clock,
        *,
        ir_structurer: IrDocumentStructurer,
    ) -> None:
        self._uow_factory = uow_factory
        self._dispatcher = dispatcher
        self._ids = ids
        self._clock = clock
        self._ir_structurer = ir_structurer

    def execute(self, command: CreateProjectCommand) -> CreateProjectResult:
        direct = command.ir is not None
        if not direct and not command.prompt.strip():
            raise ValidationError("요구사항 프롬프트가 비어 있습니다")
        if not command.title.strip():
            raise ValidationError("프로젝트 제목이 비어 있습니다")
        structured = self._structure_document(command) if direct else None

        with self._uow_factory() as uow:
            project = Project(
                project_id=self._ids.new_id("prj"),
                title=command.title,
                created_at=self._clock.now(),
            )
            version = VideoVersion(
                version_id=self._ids.new_id("ver"),
                project_id=project.project_id,
                state=VersionState.STRUCTURING if structured is None else VersionState.DRAFT,
                created_at=self._clock.now(),
                source_prompt=_source_prompt(command, direct),
                target_duration_ms=command.target_duration_ms,
                seed=command.seed,
                ir=None if structured is None else structured.ir,
            )
            uow.projects.add(project)
            uow.versions.add(version)
            for ref in command.reference_images:
                uow.reference_images.add(
                    ReferenceImage(
                        reference_image_id=self._ids.new_id("img"),
                        version_id=version.version_id,
                        image_uri=ref.image_uri,
                        entity_key=ref.entity_key,
                    )
                )
            uow.commit()

        if structured is None:
            # 커밋 이후에 작업을 넘긴다: 워커가 아직 저장되지 않은 버전을 읽는 일이 없도록.
            self._dispatcher.dispatch(
                Job(STRUCTURE_REQUIREMENTS, {"version_id": version.version_id})
            )
        return CreateProjectResult(
            project_id=project.project_id,
            version_id=version.version_id,
            state=version.state,
            repair_notes=() if structured is None else structured.repair_notes,
        )

    def _structure_document(self, command: CreateProjectCommand) -> StructuredDocument:
        """IR 직접 입력: 보정 -> 검증 -> 버전의 다른 값과 맞는지 확인 (자연어 구조화 작업과 같은 규칙)."""
        assert command.ir is not None
        structured = self._ir_structurer.structure_document(command.ir)
        check_reference_entities((r.entity_key for r in command.reference_images), structured.ir)
        check_target_duration(structured.ir.total_duration_ms, command.target_duration_ms)
        return structured


def _source_prompt(command: CreateProjectCommand, direct: bool) -> str | None:
    """원문 요구사항. IR 직접 입력에서는 글을 함께 보냈을 때만 남긴다."""
    if direct and not command.prompt.strip():
        return None
    return command.prompt
