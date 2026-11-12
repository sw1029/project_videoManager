"""/api/versions/{version_id} 계열 엔드포인트 (US-01 / US-03 / US-04 / US-06)"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from frame.application.dto import CreateRevisionCommand
from frame.config.container import Container
from frame.domain.enums import ChangeScope
from frame.domain.services.propagation import AttributeChange

from .. import mappers
from ..dependencies import get_container
from ..media import artifact_response
from ..rate_limit import enforce_rate_limit
from ..schemas import (
    AcceptGenerationOut,
    ChecksOut,
    CreateRevisionIn,
    CreateRevisionOut,
    EntityTracksOut,
    ErrorOut,
    VersionComparisonOut,
    VersionOut,
)

router = APIRouter(prefix="/versions", tags=["versions"])
ContainerDep = Annotated[Container, Depends(get_container)]

MEDIA_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": "실제 모델은 video/mp4, Fake 어댑터는 application/json 매니페스트. 파일 저장소는 Range 지원",
        "content": {"video/mp4": {}, "application/json": {}},
    },
    404: {"model": ErrorOut, "description": "NOT_FOUND 또는 MEDIA_NOT_AVAILABLE(생성 전·실패)"},
}


@router.get(
    "/{version_id}",
    response_model=VersionOut,
    responses={404: {"model": ErrorOut}},
    summary="중간표현·처리 상태·결과 영상 경로(실패 시 오류 정보)를 조회한다",
)
def get_version(version_id: str, container: ContainerDep) -> VersionOut:
    return mappers.version_out(
        container.queries.get_version(version_id),
        api_prefix=container.settings.api_prefix,
        media_type_of=container.artifact_store.content_type,
    )


@router.get(
    "/{version_id}/video",
    response_class=Response,
    responses=MEDIA_RESPONSES,
    summary="결과 영상을 내려준다 (VersionOut.video_url)",
)
def get_video(version_id: str, container: ContainerDep) -> Response:
    return artifact_response(container.artifact_store, container.queries.get_video_uri(version_id))


@router.get(
    "/{version_id}/scenes/{scene_key}/clip",
    response_class=Response,
    responses=MEDIA_RESPONSES,
    summary="장면 클립을 내려준다 (SceneOut.clip_url)",
)
def get_clip(version_id: str, scene_key: str, container: ContainerDep) -> Response:
    return artifact_response(
        container.artifact_store, container.queries.get_clip_uri(version_id, scene_key)
    )


@router.post(
    "/{version_id}/revisions",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateRevisionOut,
    responses={
        404: {"model": ErrorOut},
        409: {"model": ErrorOut},
        422: {"model": ErrorOut},
        429: {"model": ErrorOut},
    },
    dependencies=[Depends(enforce_rate_limit)],
    summary="선택 객체(또는 entity_key 생략 시 장면 수준 조건)를 바꾼 새 DRAFT 버전을 만든다 (원본 보존)",
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
                current_value=body.current_value,
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
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 429: {"model": ErrorOut}},
    dependencies=[Depends(enforce_rate_limit)],
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


@router.get(
    "/{version_id}/comparison",
    response_model=VersionComparisonOut,
    responses={
        404: {"model": ErrorOut, "description": "NOT_FOUND (버전 또는 비교 기준 버전)"},
        409: {"model": ErrorOut, "description": "VERSION_NOT_COMPARABLE (중간표현이 없는 버전)"},
        422: {
            "model": ErrorOut,
            "description": "부모 없는 원본에 against 생략, 다른 프로젝트 버전",
        },
    },
    summary="두 버전(기본: 부모)을 장면별 클립 재사용·바뀐 요구조건·검수 요약 차이로 비교한다",
)
def compare_versions(
    version_id: str,
    container: ContainerDep,
    against: Annotated[
        str | None,
        Query(min_length=1, description="비교 기준 버전 ID. 생략하면 부모 버전과 비교한다"),
    ] = None,
) -> VersionComparisonOut:
    return mappers.comparison_out(
        container.queries.compare_versions(version_id, against),
        api_prefix=container.settings.api_prefix,
        media_type_of=container.artifact_store.content_type,
    )
