"""유스케이스 입출력 DTO. Presentation 계층은 이 객체만 보고 HTTP 스키마로 변환한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from frame.domain.checks import CheckResult
from frame.domain.enums import RevisionKind, Verdict, VersionState
from frame.domain.metrics import CheckSummary
from frame.domain.models import Project, ReferenceImage, VideoVersion
from frame.domain.revision import AttributeChange, KeepItem
from frame.domain.services.comparison import RateDelta, VersionComparison
from frame.domain.tracking import TrackRecord

from .ports.structurer import ReferenceImageInput


@dataclass(frozen=True)
class CreateProjectCommand:
    title: str
    prompt: str  # 자연어(또는 DSL) 요구사항. ir 을 주면 비워도 되고, 주면 원문으로만 보관한다
    reference_images: tuple[ReferenceImageInput, ...] = ()
    target_duration_ms: int | None = None
    seed: int | None = None
    # 블록 입력 화면이 조립한 IR 문서 (IR.to_dict 형식, #10). 주면 LLM 없이 보정·검증만 거쳐 바로 DRAFT 가 된다
    ir: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CreateProjectResult:
    project_id: str
    version_id: str
    state: VersionState  # 자연어: STRUCTURING / IR 직접 입력: DRAFT
    repair_notes: tuple[str, ...] = ()  # IR 직접 입력에서 보정이 고친 것


@dataclass(frozen=True)
class CreateRevisionCommand:
    version_id: str
    change: AttributeChange
    # CHANGE: 값을 바꾼다 / REPAIR: 값은 그대로 두고 그 요구조건이 걸린 장면을 다시 만든다 (ADR-0017)
    kind: RevisionKind = RevisionKind.CHANGE
    keep: tuple[KeepItem, ...] = ()  # 다시 만드는 장면에서 그대로 둘 요소
    request_text: str | None = None  # 사용자가 쓴 요청 원문 (기록·표시용)


@dataclass(frozen=True)
class CreateRevisionResult:
    version_id: str
    parent_version_id: str
    state: VersionState
    # 다시 만들 장면. CHANGE: 조건이 바뀐 장면 / REPAIR: 다시 맞출 장면
    changed_scene_keys: tuple[str, ...]
    kind: RevisionKind = RevisionKind.CHANGE


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


@dataclass(frozen=True)
class VersionOverview:
    """전후 비교와 대시보드가 함께 쓰는 버전 요약 (상태·결과 영상·검수 요약)."""

    version: VideoVersion
    summary: CheckSummary
    requirement_verdicts: Mapping[str, Verdict]  # requirement_key -> REQUIREMENT 검수 판정


@dataclass(frozen=True)
class VersionComparisonView:
    before: VersionOverview  # 비교 기준 (against, 생략하면 부모)
    after: VersionOverview  # 비교 대상 (요청한 버전)
    against_is_parent: bool
    comparison: VersionComparison
    checks_delta: RateDelta  # after − before


@dataclass(frozen=True)
class DashboardVersion:
    overview: VersionOverview
    depth: int  # 원본(루트) = 0
    child_version_ids: tuple[str, ...]
    scene_count: int | None  # IR 이 없으면 None
    changed_scene_count: int | None  # 부모 대비 조건이 바뀐 장면 수 (원본이거나 IR 이 없으면 None)


@dataclass(frozen=True)
class ProjectDashboardView:
    project: Project
    versions: tuple[DashboardVersion, ...]  # 생성 순
    state_counts: Mapping[VersionState, int]  # 모든 상태를 키로 둔다 (0 포함)
    latest_version_id: str | None
    latest_completed_version_id: str | None
