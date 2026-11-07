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
