"""도메인 Enum 정의. ERD 의 Enum 허용값과 1:1 로 대응한다."""

from enum import StrEnum


class VersionState(StrEnum):
    """VIDEO_VERSION.state"""

    STRUCTURING = "STRUCTURING"
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"  # 처리 완료 (검수 판정 FAIL/UNDETERMINED 여도 COMPLETED)
    FAILED = "FAILED"  # 실행 오류


class TrackingStatus(StrEnum):
    """SCENE_ENTITY.tracking_status"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CheckType(StrEnum):
    """CHECK_RESULT.check_type"""

    REQUIREMENT = "REQUIREMENT"  # 요구사항 반영 여부
    CONSISTENCY = "CONSISTENCY"  # 타임라인 내 객체 속성 일관성
    PRESERVATION = "PRESERVATION"  # 수정 시 비대상 요소 보존


class Verdict(StrEnum):
    """CHECK_RESULT.verdict"""

    PASS = "PASS"
    FAIL = "FAIL"  # 검수 기준 미달 (실행 오류 FAILED 와 구분)
    UNDETERMINED = "UNDETERMINED"  # 미판정 - 통과로 집계하지 않는다


class EntityKind(StrEnum):
    """구조화된 중간표현에서 객체의 종류 (인물/동물/소품/배경/문구)."""

    PERSON = "PERSON"
    ANIMAL = "ANIMAL"
    PROP = "PROP"
    BACKGROUND = "BACKGROUND"
    TEXT = "TEXT"
    OTHER = "OTHER"


class ChangeScope(StrEnum):
    """속성 변경의 적용 범위 (US-01 AC-02)."""

    SCENES = "SCENES"  # 지정한 장면에만 반영
    ALL_APPEARANCES = "ALL_APPEARANCES"  # 객체가 등장하는 모든 장면에 반영


class ReferenceOrigin(StrEnum):
    """REFERENCE_IMAGE 의 출처 (ERD 확장, ADR-0015)."""

    USER = "USER"  # 사용자가 올린 참조 이미지
    DERIVED = "DERIVED"  # 장면 클립에서 뽑은 객체의 정체성 기준 이미지
