"""POST /api/projects (US-06, 글 또는 블록 IR #10), GET /api/projects/{project_id} · /dashboard (US-04)"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from frame.application.dto import CreateProjectCommand
from frame.application.ports.structurer import ReferenceImageInput
from frame.config.container import Container
from frame.domain.enums import VersionState

from .. import mappers
from ..dependencies import get_container
from ..rate_limit import enforce_rate_limit
from ..schemas import (
    CreateProjectIn,
    CreateProjectOut,
    ErrorOut,
    ProjectDashboardOut,
    ProjectOut,
)

router = APIRouter(prefix="/projects", tags=["projects"])
ContainerDep = Annotated[Container, Depends(get_container)]


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CreateProjectOut,
    responses={
        201: {
            "model": CreateProjectOut,
            "description": "IR 직접 입력: 구조화 없이 바로 DRAFT 를 만들었다 (repair_notes 에 보정 내용)",
        },
        422: {"model": ErrorOut},
        429: {"model": ErrorOut},
    },
    dependencies=[Depends(enforce_rate_limit)],
    summary="요구사항(글 또는 블록 IR)·참조 이미지를 접수한다: 글은 구조화를 시작하고(202), "
    "IR 은 보정·검증만 거쳐 바로 DRAFT 를 만든다(201)",
)
def create_project(
    body: CreateProjectIn, container: ContainerDep, response: Response
) -> CreateProjectOut:
    result = container.create_project.execute(
        CreateProjectCommand(
            title=body.title,
            prompt=body.prompt or "",
            reference_images=tuple(
                ReferenceImageInput(image_uri=r.image_uri, entity_key=r.entity_key)
                for r in body.reference_images
            ),
            target_duration_ms=body.target_duration_ms,
            seed=body.seed,
            ir=body.ir.model_dump() if body.ir is not None else None,
        )
    )
    if result.state is VersionState.DRAFT:  # IR 직접 입력은 요청 안에서 다 만들어졌다
        response.status_code = status.HTTP_201_CREATED
    return CreateProjectOut(
        project_id=result.project_id,
        version_id=result.version_id,
        state=str(result.state),
        repair_notes=list(result.repair_notes),
    )


@router.get(
    "/{project_id}",
    response_model=ProjectOut,
    responses={404: {"model": ErrorOut}},
    summary="프로젝트와 버전 목록을 조회한다",
)
def get_project(project_id: str, container: ContainerDep) -> ProjectOut:
    return mappers.project_out(container.queries.get_project(project_id))


@router.get(
    "/{project_id}/dashboard",
    response_model=ProjectDashboardOut,
    responses={404: {"model": ErrorOut}},
    summary="버전 트리와 버전별 상태·검수 요약·바뀐 장면 수, 프로젝트 집계를 한 번에 조회한다",
)
def get_dashboard(project_id: str, container: ContainerDep) -> ProjectDashboardOut:
    return mappers.dashboard_out(
        container.queries.get_dashboard(project_id),
        api_prefix=container.settings.api_prefix,
        media_type_of=container.artifact_store.content_type,
    )
