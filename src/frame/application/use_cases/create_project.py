"""US-06 1단계: 요구사항·참조 이미지를 접수하고 초기 버전(STRUCTURING)을 만든 뒤 구조화 작업을 넘긴다."""

from __future__ import annotations

from frame.domain.enums import VersionState
from frame.domain.errors import ValidationError
from frame.domain.models import Project, ReferenceImage, VideoVersion

from ..dto import CreateProjectCommand, CreateProjectResult
from ..jobs import STRUCTURE_REQUIREMENTS
from ..ports.common import Clock, IdGenerator
from ..ports.jobs import Job, JobDispatcher
from ..ports.unit_of_work import UnitOfWorkFactory


class CreateProject:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        dispatcher: JobDispatcher,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._dispatcher = dispatcher
        self._ids = ids
        self._clock = clock

    def execute(self, command: CreateProjectCommand) -> CreateProjectResult:
        if not command.prompt.strip():
            raise ValidationError("요구사항 프롬프트가 비어 있습니다")
        if not command.title.strip():
            raise ValidationError("프로젝트 제목이 비어 있습니다")

        with self._uow_factory() as uow:
            project = Project(
                project_id=self._ids.new_id("prj"),
                title=command.title,
                created_at=self._clock.now(),
            )
            version = VideoVersion(
                version_id=self._ids.new_id("ver"),
                project_id=project.project_id,
                state=VersionState.STRUCTURING,
                created_at=self._clock.now(),
                source_prompt=command.prompt,
                target_duration_ms=command.target_duration_ms,
                seed=command.seed,
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

        # 커밋 이후에 작업을 넘긴다: 워커가 아직 저장되지 않은 버전을 읽는 일이 없도록.
        self._dispatcher.dispatch(Job(STRUCTURE_REQUIREMENTS, {"version_id": version.version_id}))
        return CreateProjectResult(
            project_id=project.project_id, version_id=version.version_id, state=version.state
        )
