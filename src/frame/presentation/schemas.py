"""HTTP 요청/응답 스키마 (pydantic). 도메인 객체는 presentation/mappers.py 에서 변환한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---- 요청 ----------------------------------------------------------------


class ReferenceImageIn(BaseModel):
    image_uri: str = Field(min_length=1)
    entity_key: str | None = None


class CreateProjectIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=1)
    reference_images: list[ReferenceImageIn] = Field(default_factory=list)
    target_duration_ms: int | None = Field(default=None, gt=0)
    seed: int | None = None


class CreateRevisionIn(BaseModel):
    entity_key: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    new_value: str = Field(min_length=1)
    scope: Literal["SCENES", "ALL_APPEARANCES"] = "ALL_APPEARANCES"
    scene_keys: list[str] = Field(default_factory=list)


# ---- 응답 ----------------------------------------------------------------


class ErrorOut(BaseModel):
    error_code: str
    error_message: str


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


class SceneOut(BaseModel):
    scene_key: str
    planned_start_ms: int
    planned_end_ms: int
    description: str
    entity_keys: list[str]
    requirements: list[RequirementOut]
    clip_uri: str | None


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


class VersionOut(BaseModel):
    version_id: str
    project_id: str
    parent_version_id: str | None
    state: str
    source_prompt: str | None
    seed: int | None
    frozen_fingerprint: str | None
    ir: IROut | None
    video_uri: str | None
    error_code: str | None
    error_message: str | None
    reference_images: list[ReferenceImageOut]
    created_at: datetime


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
