"""한 번만 과금하기 위한 기록 - 생성 캐시와 작업 원장 (ADR-0013, 0021).

맡는 개념
    유료 생성은 제출할 때 과금된다. 같은 조건 · 같은 설정의 장면을 두 번 제출하지 않도록 두 가지를 같은 키로 남긴다:
    생성 캐시는 끝난 결과(클립)를, 작업 원장은 제출한 작업(끝나기 전의 것까지)을 적는다.
    둘 다 산출물 저장소에 두어 프로세스를 다시 시작해도 남는다. 키로 주소를 알 수 없는 저장소에서는 메모리에 둔다.

밖에서 쓰는 이름
    generation_key                캐시 · 원장의 키: 결과 키 + 생성기 프로필
    ClipCache · CachedClip        생성 캐시의 계약과 항목
        ArtifactClipCache · InMemoryClipCache    구현 (산출물 저장소 · 메모리). 고르기는 build_clip_cache
    JobLedger · JobRecord         작업 원장의 계약과 항목
        ArtifactJobLedger · InMemoryJobLedger    구현 (산출물 저장소 · 메모리). 고르기는 build_job_ledger
    SUBMITTED · COMPLETED · FAILED · RELEASED    원장 항목의 상태
    CACHE_PREFIX · LEDGER_PREFIX  산출물 저장소에서의 자리

모듈 지도 (안쪽 모듈은 _ 로 시작하고 이 패키지 밖에서 직접 import 하지 않는다)
    _clip_cache.py    생성 캐시
    _job_ledger.py    작업 원장
같은 키의 동시 요청을 하나로 줄이는 키별 잠금은 frame.adapters.shared.locking 에 있다.
"""

from ._clip_cache import (
    CACHE_PREFIX,
    ArtifactClipCache,
    CachedClip,
    ClipCache,
    InMemoryClipCache,
    build_clip_cache,
    generation_key,
)
from ._job_ledger import (
    COMPLETED,
    FAILED,
    LEDGER_PREFIX,
    RELEASED,
    SUBMITTED,
    ArtifactJobLedger,
    InMemoryJobLedger,
    JobLedger,
    JobRecord,
    build_job_ledger,
)

__all__ = [
    "CACHE_PREFIX",
    "COMPLETED",
    "FAILED",
    "LEDGER_PREFIX",
    "RELEASED",
    "SUBMITTED",
    "ArtifactClipCache",
    "ArtifactJobLedger",
    "CachedClip",
    "ClipCache",
    "InMemoryClipCache",
    "InMemoryJobLedger",
    "JobLedger",
    "JobRecord",
    "build_clip_cache",
    "build_job_ledger",
    "generation_key",
]
