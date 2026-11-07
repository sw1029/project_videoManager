"""유스케이스 입출력 DTO. Presentation 계층은 이 객체만 보고 HTTP 스키마로 변환한다."""

from __future__ import annotations

from dataclasses import dataclass

from frame.domain.checks import CheckResult
from frame.domain.enums import VersionState
from frame.domain.metrics import CheckSummary
from frame.domain.models import Project, ReferenceImage, VideoVersion
from frame.domain.services.propagation import AttributeChange
from frame.domain.tracking import TrackRecord

from .ports.structurer import ReferenceImageInput


@dataclass(frozen=True)
class CreateProjectCommand:
    title: str
    prompt: str
    reference_images: tuple[ReferenceImageInput, ...] = ()
    target_duration_ms: int | None = None
    seed: int | None = None


@dataclass(frozen=True)
class CreateProjectResult:
    project_id: str
    version_id: str
    state: VersionState


@dataclass(frozen=True)
class CreateRevisionCommand:
    version_id: str
    change: AttributeChange


@dataclass(frozen=True)
class CreateRevisionResult:
    version_id: str
    parent_version_id: str
    state: VersionState
    changed_scene_keys: tuple[str, ...]


@dataclass(frozen=True)
class AcceptGenerationResult:
    version_id: str
    state: VersionState


@dataclass(frozen=True)
class ProjectDetail:
    project: Project
    versions: tuple[VideoVersion, ...]


@dataclass(frozen=True)
class VersionDetail:
    version: VideoVersion
    reference_images: tuple[ReferenceImage, ...]


@dataclass(frozen=True)
class ChecksView:
    version_id: str
    state: VersionState
    results: tuple[CheckResult, ...]
    summary: CheckSummary


@dataclass(frozen=True)
class EntityTracksView:
    version_id: str
    entity_key: str
    records: tuple[TrackRecord, ...]
