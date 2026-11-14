"""어댑터(외부 모델·저장소) 오류. code 는 VIDEO_VERSION.error_code 로 기록된다."""

# 예상하지 못한 예외의 코드: 도메인 오류(DomainError)도 어댑터 오류(AdapterError)도 아닌 예외를 기록할 때 쓴다.
# 생성 파이프라인의 실패 기록, 평가 도구의 케이스 결과, 검수 단위의 미판정 사유가 이 이름을 쓴다 -
# 기록을 읽는 쪽이 한 글자로 찾는다 (쓰는 곳마다 다시 적지 않는다)
UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


class AdapterError(Exception):
    code: str = "ADAPTER_ERROR"


class StructuringError(AdapterError):
    code = "STRUCTURING_ERROR"


class GenerationError(AdapterError):
    """retryable=False: 다시 호출해도 소용없거나, 외부 API 가 이미 작업을 받아 다시 제출하면 또 과금되는 실패.

    RetryingVideoGenerator 는 retryable=False 인 오류를 재시도하지 않는다 (ADR-0011).
    """

    code = "GENERATION_ERROR"

    def __init__(self, *args: object, retryable: bool = True) -> None:
        super().__init__(*args)
        self.retryable = retryable


class GenerationTimeout(GenerationError):
    code = "GENERATION_TIMEOUT"


class TrackingError(AdapterError):
    code = "TRACKING_ERROR"


class CheckError(AdapterError):
    """검수 어댑터의 오류. 지금은 던지는 곳이 없다: 검수기는 판정하지 못한 것을 예외가 아니라
    미판정(UNDETERMINED)으로 돌려주고 (ADR-0006), 검수기 밖으로 샌 예외는 검수 묶음이 미판정으로 바꾼다.
    """

    code = "CHECK_ERROR"


class StorageError(AdapterError):
    code = "STORAGE_ERROR"


class ConfigurationError(Exception):
    """설정 키에 해당하는 어댑터가 등록되어 있지 않을 때 (기동 시점에 실패해야 한다)."""


class LLMError(AdapterError):
    """LLM 호출 실패. code 가 VIDEO_VERSION.error_code 로 기록된다."""

    code = "LLM_ERROR"


class LLMUnavailable(LLMError):
    code = "LLM_UNAVAILABLE"  # 연결·인증·모델 없음 등 재시도로 해결되지 않는 실패


class LLMTimeout(LLMError):
    code = "LLM_TIMEOUT"


class LLMRateLimited(LLMError):
    code = "LLM_RATE_LIMITED"


class LLMBadResponse(LLMError):
    code = "LLM_BAD_RESPONSE"  # 거부·빈 응답·형식 위반
