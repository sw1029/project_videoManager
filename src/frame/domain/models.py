"""도메인 Entity (ERD 의 테이블과 대응). 상태 규칙은 Entity 메서드에서만 바뀐다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .artifacts import GeneratedClip
from .enums import VersionState
from .errors import ValidationError
from .ir import IntermediateRepresentation
from .progress import PipelineProgress
from .state_machine import assert_transition


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

    # ---- 상태 규칙 ----------------------------------------------------------
    def transition_to(self, new_state: VersionState) -> None:
        assert_transition(self.state, new_state)
        self.state = new_state

    def mark_failed(self, error_code: str, error_message: str) -> None:
        self.transition_to(VersionState.FAILED)
        self.error_code = error_code
        self.error_message = error_message

    def freeze_conditions(self) -> None:
        """생성 접수 시 조건(IR)을 고정한다. 이후 IR 변경은 새 버전으로만 가능하다."""
        self.frozen_fingerprint = self.require_ir().fingerprint()

    @property
    def is_frozen(self) -> bool:
        return self.frozen_fingerprint is not None

    def require_ir(self) -> IntermediateRepresentation:
        if self.ir is None:
            raise ValidationError("버전에 중간표현(IR)이 없습니다", version_id=self.version_id)
        return self.ir
