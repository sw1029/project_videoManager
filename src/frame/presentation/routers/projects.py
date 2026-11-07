"""POST /api/projects (US-06), GET /api/projects/{project_id} (US-04)"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from frame.application.dto import CreateProjectCommand
from frame.application.ports.structurer import ReferenceImageInput
from frame.config.container import Container

from .. import mappers
from ..dependencies import get_container
from ..schemas import CreateProjectIn, CreateProjectOut, ErrorOut, ProjectOut

router = APIRouter(prefix="/projects", tags=["projects"])
ContainerDep = Annotated[Container, Depends(get_container)]


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CreateProjectOut,
    responses={422: {"model": ErrorOut}},
    summary="요구사항·참조 이미지를 접수하고 초기 버전의 구조화를 시작한다",
)
def create_project(body: CreateProjectIn, container: ContainerDep) -> CreateProjectOut:
    result = container.create_project.execute(
        CreateProjectCommand(
            title=body.title,
            prompt=body.prompt,
            reference_images=tuple(
                ReferenceImageInput(image_uri=r.image_uri, entity_key=r.entity_key)
                for r in body.reference_images
            ),
            target_duration_ms=body.target_duration_ms,
            seed=body.seed,
        )
    )
    return CreateProjectOut(
        project_id=result.project_id, version_id=result.version_id, state=str(result.state)
    )


@router.get(
    "/{project_id}",
    response_model=ProjectOut,
    responses={404: {"model": ErrorOut}},
    summary="프로젝트와 버전 목록을 조회한다",
)
def get_project(project_id: str, container: ContainerDep) -> ProjectOut:
    return mappers.project_out(container.queries.get_project(project_id))
