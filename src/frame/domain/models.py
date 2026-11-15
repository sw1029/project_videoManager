"""도메인 Entity (ERD 의 테이블과 대응). 상태 규칙은 Entity 메서드에서만 바뀐다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .artifacts import GeneratedClip
from .enums import ReferenceOrigin, VersionState
from .errors import ValidationError, VersionNotRevisable
from .ir import IntermediateRepresentation
from .progress import PipelineProgress
from .revision import RevisionIntent
from .state_machine import REVISABLE_STATES, assert_transition


@dataclass
class Project:
    project_id: str
    title: str
    created_at: datetime


@dataclass
class ReferenceImage:
    reference_image_id: str
    version_id: str
    image_uri: str
    entity_key: str | None = None  # 특정 객체의 시각 참조이면 지정 (ERD: entity_id?)
    # ---- 정체성 기준 이미지 (ERD 확장, ADR-0015). 사용자 참조(USER)는 아래 값이 비어 있다 ----
    origin: ReferenceOrigin = ReferenceOrigin.USER
    source_scene_key: str | None = None  # 기준을 뽑은 장면
    source_fingerprint: str | None = None  # 기준을 뽑은 클립의 조건 fingerprint
    appearance_fingerprint: str | None = None  # 뽑을 때 그 장면에서 객체의 외형 조건 해시
    description: str = ""  # 기준 이미지에서 읽은 외형 설명 (프롬프트 보강용, 없어도 된다)

    @property
    def is_identity(self) -> bool:
        return self.origin is ReferenceOrigin.DERIVED and self.entity_key is not None


@dataclass
class VideoVersion:
    version_id: str
    project_id: str
    state: VersionState
    created_at: datetime
    parent_version_id: str | None = None
    source_prompt: str | None = None  # 원문 요구사항 (재현성·재구조화용, ERD 확장)
    target_duration_ms: int | None = None
    seed: int | None = None  # 생성 조건의 일부. 수정 버전은 부모의 seed 를 물려받는다
    ir: IntermediateRepresentation | None = None
    video_uri: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    frozen_fingerprint: str | None = None  # 생성 접수 시 고정된 IR 해시
    clips: dict[str, GeneratedClip] = field(default_factory=dict)  # scene_key -> clip (ERD 확장)
    # 생성·추적·검수 단계와 장면 진행 (ERD 확장). 파이프라인이 처리 도중과 끝에 저장한다. 실행 전에는 None
    progress: PipelineProgress | None = None
    # 장면별 seed 재지정 (ERD 확장, ADR-0017). 요구조건은 그대로 두고 다시 만드는 장면(REPAIR)에 쓴다.
    # seed 처럼 생성 조건의 일부이고 수정 버전이 물려받는다 (그래야 그 장면이 다음 버전에서 재사용된다)
    scene_seeds: dict[str, int] = field(default_factory=dict)
    # 이 수정 버전이 하려는 것과 그대로 둘 요소 (ERD 확장, ADR-0017). 원본 버전은 None
    intent: RevisionIntent | None = None
    # 실행 기록 (ERD 확장, #2). 키워드로만 준다
    # queued_at: 마지막으로 QUEUED 가 된 시각 / started_at: 마지막 실행(PROCESSING) 시작 시각
    # attempt: 실행 시도 횟수 (워커 유실 뒤 재시도 포함)
    queued_at: datetime | None = field(default=None, kw_only=True)
    started_at: datetime | None = field(default=None, kw_only=True)
    attempt: int = field(default=0, kw_only=True)

    def seed_for(self, scene_key: str) -> int | None:
        """그 장면을 생성할 때 쓰는 seed: 장면별로 다시 지정했으면 그 값, 아니면 버전의 seed."""
        return self.scene_seeds.get(scene_key, self.seed)

    # ---- 상태 규칙 ----------------------------------------------------------
    def transition_to(self, new_state: VersionState) -> None:
        assert_transition(self.state, new_state)
        self.state = new_state

    def mark_failed(self, error_code: str, error_message: str) -> None:
        self.transition_to(VersionState.FAILED)
        self.error_code = error_code
        self.error_message = error_message

    def record_queued(self, at: datetime) -> None:
        """생성 접수(DRAFT->QUEUED) 또는 복구 재시도(PROCESSING->QUEUED) 시각."""
        self.queued_at = at

    def record_started(self, at: datetime) -> None:
        """실행 시작(QUEUED->PROCESSING): 시각을 남기고 시도 횟수를 1 늘린다 (재시도 상한·리스 판단의 근거)."""
        self.started_at = at
        self.attempt += 1

    def freeze_conditions(self) -> None:
        """생성 접수 시 조건(IR)을 고정한다. 이후 IR 변경은 새 버전으로만 가능하다."""
        self.frozen_fingerprint = self.require_ir().fingerprint()

    @property
    def is_frozen(self) -> bool:
        return self.frozen_fingerprint is not None

    def ensure_revisable(self) -> IntermediateRepresentation:
        """수정 버전의 바탕이 될 수 있는 버전인가: 처리 중이 아니고 중간표현이 있다. 아니면 VersionNotRevisable.

        수정 버전을 만들 때와 변경 계획을 미리 볼 때가 같은 규칙을 본다. 바탕이 될 중간표현을 돌려준다.
        """
        if self.state not in REVISABLE_STATES or self.ir is None:
            raise VersionNotRevisable(
                f"상태 {self.state} 인 버전은 수정할 수 없습니다", state=self.state
            )
        return self.ir

    def require_ir(self) -> IntermediateRepresentation:
        if self.ir is None:
            raise ValidationError("버전에 중간표현(IR)이 없습니다", version_id=self.version_id)
        return self.ir
