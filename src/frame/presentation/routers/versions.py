"""/api/versions/{version_id} 계열 엔드포인트 (US-01 / US-03 / US-04 / US-06)"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from frame.application.dto import CreateRevisionCommand
from frame.config.container import Container
from frame.domain.enums import ChangeScope
from frame.domain.services.propagation import AttributeChange

from .. import mappers
from ..dependencies import get_container
from ..schemas import (
    AcceptGenerationOut,
    ChecksOut,
    CreateRevisionIn,
    CreateRevisionOut,
    EntityTracksOut,
    ErrorOut,
    VersionOut,
)

router = APIRouter(prefix="/versions", tags=["versions"])
ContainerDep = Annotated[Container, Depends(get_container)]


@router.get(
    "/{version_id}",
    response_model=VersionOut,
    responses={404: {"model": ErrorOut}},
    summary="중간표현·처리 상태·결과 영상(실패 시 오류 정보)을 조회한다",
)
def get_version(version_id: str, container: ContainerDep) -> VersionOut:
    return mappers.version_out(container.queries.get_version(version_id))


@router.post(
    "/{version_id}/revisions",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateRevisionOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="선택 객체의 속성을 바꾼 새 DRAFT 버전을 만든다 (원본 보존)",
)
def create_revision(
    version_id: str, body: CreateRevisionIn, container: ContainerDep
) -> CreateRevisionOut:
    result = container.create_revision.execute(
        CreateRevisionCommand(
            version_id=version_id,
            change=AttributeChange(
                entity_key=body.entity_key,
                predicate=body.predicate,
                new_value=body.new_value,
                scope=ChangeScope(body.scope),
                scene_keys=tuple(body.scene_keys),
            ),
        )
    )
    return CreateRevisionOut(
        version_id=result.version_id,
        parent_version_id=result.parent_version_id,
        state=str(result.state),
        changed_scene_keys=list(result.changed_scene_keys),
    )


@router.post(
    "/{version_id}/generations",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptGenerationOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
    summary="DRAFT 버전의 생성·추적·검수를 비동기로 접수한다 (DRAFT 가 아니면 409)",
)
def accept_generation(version_id: str, container: ContainerDep) -> AcceptGenerationOut:
    result = container.accept_generation.execute(version_id)
    return AcceptGenerationOut(version_id=result.version_id, state=str(result.state))


@router.get(
    "/{version_id}/checks",
    response_model=ChecksOut,
    responses={404: {"model": ErrorOut}},
    summary="요구사항 반영·일관성·비대상 보존 판정과 경고 구간을 조회한다",
)
def get_checks(version_id: str, container: ContainerDep) -> ChecksOut:
    return mappers.checks_out(container.queries.get_checks(version_id))


@router.get(
    "/{version_id}/entities/{entity_id}/tracks",
    response_model=EntityTracksOut,
    responses={404: {"model": ErrorOut}},
    summary="선택 객체의 등장 구간·위치·추적 상태를 조회한다",
)
def get_entity_tracks(version_id: str, entity_id: str, container: ContainerDep) -> EntityTracksOut:
    return mappers.tracks_out(container.queries.get_entity_tracks(version_id, entity_id))
