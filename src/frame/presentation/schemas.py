"""HTTP 요청/응답 스키마 (pydantic). 도메인 객체는 presentation/mappers.py 에서 변환한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---- 입력 제한 (공개 데모에서 LLM 비용·저장 용량을 지키기 위한 상한) ----------------

MAX_PROMPT_CHARS = 4000
MAX_REFERENCE_IMAGES = 10
MAX_TARGET_DURATION_MS = 300_000  # 5분 (숏폼 대상)

# ---- 요청 ----------------------------------------------------------------


class ReferenceImageIn(BaseModel):
    image_uri: str = Field(min_length=1, max_length=1024)
    entity_key: str | None = Field(default=None, max_length=64)


class CreateProjectIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    reference_images: list[ReferenceImageIn] = Field(
        default_factory=list, max_length=MAX_REFERENCE_IMAGES
    )
    target_duration_ms: int | None = Field(default=None, gt=0, le=MAX_TARGET_DURATION_MS)
    seed: int | None = None


class CreateRevisionIn(BaseModel):
    # None 이면 장면 수준 조건(장소·조명·분위기·카메라)을 바꾼다
    entity_key: str | None = Field(default=None, min_length=1, max_length=64)
    predicate: str = Field(min_length=1, max_length=64)
    new_value: str = Field(min_length=1, max_length=300)
    scope: Literal["SCENES", "ALL_APPEARANCES"] = "ALL_APPEARANCES"
    scene_keys: list[str] = Field(default_factory=list, max_length=50)
    # 같은 술어 조건이 여러 개(관계)일 때 바꿀 대상의 현재 값
    current_value: str | None = Field(default=None, max_length=300)


# ---- 응답 ----------------------------------------------------------------


class ErrorDetailOut(BaseModel):
    loc: str  # 예: body.title
    message: str


class ErrorOut(BaseModel):
    """모든 오류 응답의 공통 형식."""

    error_code: str
    error_message: str
    details: list[ErrorDetailOut] | None = None  # 요청 스키마 검증 오류일 때만


class HealthAdaptersOut(BaseModel):
    structurer: str
    generator: str
    tracker: str
    checkers: list[str]
    persistence: str
    jobs: str
    llm_provider: str | None  # structurer=llm 일 때만
    llm_model: str | None


class HealthOut(BaseModel):
    status: str
    version: str
    # 화면이 예시 입력을 고를 때 쓴다: dsl(규칙 기반) / natural_language(LLM) / any
    structuring_input: Literal["dsl", "natural_language", "any"]
    adapters: HealthAdaptersOut


class PredicateOut(BaseModel):
    name: str
    value_kind: str
    relational: bool
    aliases: list[str]
    description: str


class VocabularyOut(BaseModel):
    predicates: list[PredicateOut]


class CreateProjectOut(BaseModel):
    project_id: str
    version_id: str
    state: str


class VersionSummaryOut(BaseModel):
    version_id: str
    state: str
    parent_version_id: str | None
    created_at: datetime


class ProjectOut(BaseModel):
    project_id: str
    title: str
    versions: list[VersionSummaryOut]


class RequirementOut(BaseModel):
    requirement_key: str
    scene_key: str
    entity_key: str | None
    related_entity_key: str | None
    predicate: str
    expected_value: str
    value_kind: str | None  # 어휘에 등록된 술어면 COLOR/TEXT/NUMBER/POSITION/ENTITY
    relational: bool


class SceneOut(BaseModel):
    scene_key: str
    planned_start_ms: int
    planned_end_ms: int
    description: str
    entity_keys: list[str]
    requirements: list[RequirementOut]
    clip_uri: str | None  # 내부 저장소 URI (디버깅용)
    clip_url: str | None  # 브라우저가 재생·다운로드할 HTTP 경로
    clip_media_type: str | None


class EntityOut(BaseModel):
    entity_id: str
    entity_key: str
    kind: str
    description: str


class IROut(BaseModel):
    entities: list[EntityOut]
    scenes: list[SceneOut]


class ReferenceImageOut(BaseModel):
    reference_image_id: str
    image_uri: str
    entity_key: str | None


class StageProgressOut(BaseModel):
    stage: str  # generate | track | check
    status: Literal["RUNNING", "COMPLETED", "FAILED"]
    elapsed_ms: int  # 단계 시작부터 마지막 기록까지 (끝났으면 단계 소요 시간)
    done: int  # 끝낸 항목(장면) 수
    total: int  # 전체 항목 수. 0 이면 항목 단위 진행이 없는 단계 (check)
    current: str | None  # 처리 중인 장면 키. 실패했으면 실패한 장면


class ProgressOut(BaseModel):
    """생성·추적·검수 진행. 처리 중에도 장면 단위로 갱신되어 폴링으로 볼 수 있다 (실행 전 버전은 null)."""

    stage: str | None  # 마지막으로 시작한 단계 (처리 중·완료·실패)
    elapsed_ms: int  # 시작한 단계들의 소요 시간 합
    stages: list[StageProgressOut]  # 시작한 단계만, 순서대로


class VersionOut(BaseModel):
    version_id: str
    project_id: str
    parent_version_id: str | None
    state: str
    source_prompt: str | None
    seed: int | None
    frozen_fingerprint: str | None
    ir: IROut | None
    video_uri: str | None  # 내부 저장소 URI (디버깅용)
    video_url: str | None  # 브라우저가 재생할 HTTP 경로 (GET 시 Range 지원)
    video_media_type: str | None  # 실제 모델: video/mp4, Fake: application/json
    error_code: str | None
    error_message: str | None
    reference_images: list[ReferenceImageOut]
    created_at: datetime
    progress: ProgressOut | None  # 실패한 버전도 어느 단계·장면까지 했는지 남는다 (이슈 #3)


class CreateRevisionOut(BaseModel):
    version_id: str
    parent_version_id: str
    state: str
    changed_scene_keys: list[str]


class AcceptGenerationOut(BaseModel):
    version_id: str
    state: str


class WarningOut(BaseModel):
    scene_key: str
    start_ms: int
    end_ms: int
    message: str


class CheckResultOut(BaseModel):
    check_id: str
    check_type: str
    verdict: str
    score: float | None
    requirement_key: str | None
    entity_key: str | None
    scene_key: str | None
    warnings: list[WarningOut]
    detail: dict[str, Any]


class CheckSummaryOut(BaseModel):
    total: int
    passed: int
    failed: int
    undetermined: int
    requirement_reflection_rate: float | None
    consistency_rate: float | None
    preservation_rate: float | None


class ChecksOut(BaseModel):
    version_id: str
    state: str
    summary: CheckSummaryOut
    results: list[CheckResultOut]


class BoxOut(BaseModel):
    t_ms: int
    x: float
    y: float
    w: float
    h: float


class TrackSegmentOut(BaseModel):
    start_ms: int
    end_ms: int
    boxes: list[BoxOut]


class TrackRecordOut(BaseModel):
    scene_key: str
    entity_key: str
    status: str
    track_uri: str | None
    segments: list[TrackSegmentOut]


class EntityTracksOut(BaseModel):
    version_id: str
    entity_key: str
    records: list[TrackRecordOut]
