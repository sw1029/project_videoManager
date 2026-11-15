"""/api/versions/{version_id} 계열 엔드포인트 (US-01 / US-03 / US-04 / US-06)"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from frame.application.dto import CreateRevisionCommand
from frame.application.use_cases.edit_draft_ir import EditDraftIrCommand
from frame.application.use_cases.plan_revision import PlanRevisionCommand
from frame.config.container import Container
from frame.domain.enums import ChangeScope, KeepAspect, RevisionKind
from frame.domain.errors import MediaNotAvailable
from frame.domain.revision import AttributeChange, KeepItem

from .. import mappers
from ..dependencies import get_container
from ..media import artifact_response
from ..rate_limit import enforce_rate_limit
from ..schemas import (
    AcceptGenerationOut,
    ChecksOut,
    CreateRevisionIn,
    CreateRevisionOut,
    EditDraftIrOut,
    EntityTracksOut,
    ErrorOut,
    IRIn,
    RevisionPlanIn,
    RevisionPlanOut,
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
    return _version_out(container, version_id)


def _version_out(container: Container, version_id: str) -> VersionOut:
    return mappers.version_out(
        container.queries.get_version(version_id),
        api_prefix=container.settings.api_prefix,
        media_type_of=container.artifact_store.content_type,
        is_stored=_stored_in(container),
    )


@router.put(
    "/{version_id}/ir",
    response_model=EditDraftIrOut,
    responses={
        404: {"model": ErrorOut},
        409: {
            "model": ErrorOut,
            "description": "VERSION_NOT_EDITABLE (DRAFT 가 아니거나 생성이 접수되어 조건이 고정됨)",
        },
        422: {"model": ErrorOut, "description": "VALIDATION_ERROR (보정해도 규칙에 맞지 않는 IR)"},
        429: {"model": ErrorOut},
    },
    dependencies=[Depends(enforce_rate_limit)],
    summary="DRAFT 버전의 중간표현(구조화 결과)을 고친다: 보정 -> 검증 -> 저장하고 보정 메모를 돌려준다 "
    "(생성 접수 뒤에는 409, 그때는 수정 버전을 만든다)",
    description=(
        "본문은 GET /api/versions/{version_id} 의 ir 과 같은 모양이다 (읽기 전용 필드는 무시한다). "
        "객체·장면 키는 영문·숫자·밑줄로 된 64자 이하이고 영문·숫자로 시작하며, "
        "대소문자만 다른 키는 같은 키로 본다.\n\n"
        "수정 버전의 DRAFT 를 고치면 응답의 revision(수정 의도)은 처음 값 그대로다. "
        "실제로 바뀐 장면은 GET /api/versions/{version_id}/comparison 의 conditions_changed 로 본다 (ADR-0023)."
    ),
)
def edit_draft_ir(version_id: str, body: IRIn, container: ContainerDep) -> EditDraftIrOut:
    result = container.edit_draft_ir.execute(
        EditDraftIrCommand(version_id=version_id, ir=body.model_dump())
    )
    version = _version_out(container, version_id)
    return EditDraftIrOut(**version.model_dump(), repair_notes=list(result.repair_notes))


def _stored_in(container: Container):  # type: ignore[no-untyped-def]
    """참조 이미지 URI 가 산출물 저장소에 있는지 묻는 함수 (다른 곳의 주소·읽기 오류는 '없음')."""

    def is_stored(uri: str) -> bool:
        try:
            return container.artifact_store.exists(uri)
        except Exception:  # noqa: BLE001 - 저장소 밖의 주소(사용자 참조의 외부 URL 등)는 내려 줄 수 없다
            return False

    return is_stored


@router.get(
    "/{version_id}/reference-images/{reference_image_id}",
    response_class=Response,
    responses={
        200: {"description": "이미지 (image/jpeg, image/png 등)", "content": {"image/*": {}}},
        404: {
            "model": ErrorOut,
            "description": "NOT_FOUND 또는 MEDIA_NOT_AVAILABLE(저장소에 없음)",
        },
    },
    summary="참조 이미지(사용자 참조·객체의 정체성 기준 이미지)를 내려준다 (ReferenceImageOut.image_url)",
)
def get_reference_image(
    version_id: str, reference_image_id: str, container: ContainerDep
) -> Response:
    uri = container.queries.get_reference_image_uri(version_id, reference_image_id)
    if not _stored_in(container)(uri):
        raise MediaNotAvailable("이 참조 이미지는 서버 저장소에 없습니다")
    return artifact_response(container.artifact_store, uri)


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
    summary="선택 객체(또는 entity_key 생략 시 장면 수준 조건)를 바꾸거나(CHANGE), "
    "요구조건은 그대로 두고 그 장면을 다시 만드는(REPAIR) 새 DRAFT 버전을 만든다 (원본 보존)",
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
            kind=RevisionKind(body.kind),
            keep=tuple(KeepItem(KeepAspect(k.aspect), k.entity_key) for k in body.keep),
            request_text=body.request_text,
        )
    )
    return CreateRevisionOut(
        version_id=result.version_id,
        parent_version_id=result.parent_version_id,
        state=str(result.state),
        changed_scene_keys=list(result.changed_scene_keys),
        kind=str(result.kind),
    )


@router.post(
    "/{version_id}/revision-plans",
    response_model=RevisionPlanOut,
    responses={
        404: {"model": ErrorOut},
        409: {"model": ErrorOut},
        422: {
            "model": ErrorOut,
            "description": "VALIDATION_ERROR 또는 CHANGE_REQUEST_NOT_UNDERSTOOD",
        },
        429: {"model": ErrorOut},
    },
    dependencies=[Depends(enforce_rate_limit)],
    summary="수정 버전을 만들기 전에 변경 계획을 미리 본다: 글로 쓴 요청의 해석, 수정의 종류, "
    "다시 만들 장면, 어림 비용·시간 (상태는 바뀌지 않는다)",
)
def plan_revision(
    version_id: str, body: RevisionPlanIn, container: ContainerDep
) -> RevisionPlanOut:
    change = None
    if body.predicate is not None and body.new_value is not None:
        change = AttributeChange(
            entity_key=body.entity_key,
            predicate=body.predicate,
            new_value=body.new_value,
            scope=ChangeScope(body.scope or "ALL_APPEARANCES"),
            scene_keys=tuple(body.scene_keys),
            current_value=body.current_value,
        )
    plan = container.plan_revision.execute(
        PlanRevisionCommand(
            version_id=version_id,
            change=change,
            request_text=body.request_text,
            entity_key=body.entity_key,
            scope=ChangeScope(body.scope) if body.scope else None,
            scene_keys=tuple(body.scene_keys),
            kind=RevisionKind(body.kind) if body.kind else None,
            keep=tuple(KeepItem(KeepAspect(k.aspect), k.entity_key) for k in body.keep),
        )
    )
    return mappers.revision_plan_out(plan)


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
