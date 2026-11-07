"""어댑터(외부 모델·저장소) 오류. code 는 VIDEO_VERSION.error_code 로 기록된다."""


class AdapterError(Exception):
    code: str = "ADAPTER_ERROR"


class StructuringError(AdapterError):
    code = "STRUCTURING_ERROR"


class GenerationError(AdapterError):
    code = "GENERATION_ERROR"


class GenerationTimeout(GenerationError):
    code = "GENERATION_TIMEOUT"


class TrackingError(AdapterError):
    code = "TRACKING_ERROR"


class CheckError(AdapterError):
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
