"""도메인 오류. code 는 API 의 error_code 와 VIDEO_VERSION.error_code 로 그대로 노출된다."""


class DomainError(Exception):
    code: str = "DOMAIN_ERROR"

    def __init__(self, message: str = "", **context: object) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.context = context


class NotFound(DomainError):
    code = "NOT_FOUND"


class MediaNotAvailable(NotFound):
    """버전은 있지만 결과 영상·클립이 아직 없을 때 (생성 전·실패). API 404."""

    code = "MEDIA_NOT_AVAILABLE"


class ValidationError(DomainError):
    code = "VALIDATION_ERROR"


class PermissionDenied(DomainError):
    """허용되지 않은 요청 (예: 다른 기기에서 API 키 입력). API 403."""

    code = "FORBIDDEN"


class IntegrityViolation(DomainError):
    code = "INTEGRITY_VIOLATION"


class InvalidStateTransition(DomainError):
    code = "INVALID_STATE_TRANSITION"


class VersionNotGeneratable(InvalidStateTransition):
    """DRAFT 가 아닌 버전에 생성을 접수하려 할 때 (API 409)."""

    code = "VERSION_NOT_GENERATABLE"


class VersionNotRevisable(InvalidStateTransition):
    """중간표현이 없거나 처리 중인 버전을 수정하려 할 때 (API 409)."""

    code = "VERSION_NOT_REVISABLE"


class VersionNotEditable(InvalidStateTransition):
    """DRAFT 가 아니거나 조건이 고정된 버전의 중간표현을 고치려 할 때 (API 409, #11)."""

    code = "VERSION_NOT_EDITABLE"
