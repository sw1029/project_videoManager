"""장면 클립 생성 (VideoGenerator 포트).

맡는 개념
    장면 하나의 클립을 만든다. 실제 제공자(OpenRouter 영상 API)와 가짜 구현이 있고, 생성기를 감싸는 복원력
    (재시도 · 시간 제한 · 결과 캐시)과 한 번만 과금하기 위한 기록(생성 캐시 · 작업 원장)이 함께 있다.
    만든 클립을 잇는 조립기는 다른 포트다 (adapters/assembler/).

밖에서 쓰는 이름
    OpenRouterVideoGenerator    OpenRouter 영상 API (FRAME_GENERATOR=external_api, ADR-0011)
    FakeVideoGenerator          결정적 가짜 구현 (영상 대신 결과 정보를 저장한다)
    DiffusersVideoGenerator     뼈대 - 구현이 아직 없다. FRAME_GENERATOR=diffusers 는 기동에서 거절한다
    CachingVideoGenerator · RetryingVideoGenerator · TimeoutVideoGenerator
                                생성기를 감싸는 데코레이터: 결과 캐시 · 재시도 · 시간 제한 (ADR-0012, 0013)
    DelegatingVideoGenerator    데코레이터의 바탕 (profile · result_key · preview 를 감싼 생성기에 넘긴다)

모듈 지도
    openrouter/    OpenRouter 생성기 (하위 패키지): 생성기 클래스, 프롬프트, 모델 정보, 키, 출력 크기
    resilience     복원력: 생성기를 감싸는 재시도 · 시간 제한 · 결과 캐시 (Decorator)
    abandonment    시간 제한으로 버려진 작업 스레드가 더 제출하지 않게 하는 신호
    records/       한 번만 과금하기 위한 기록 (하위 패키지): 생성 캐시(ClipCache) · 작업 원장(JobLedger) · 그 키
    normalizer     클립 정규화(FfmpegVideoNormalizer): 모델이 돌려준 클립을 한 규격으로 맞춘다
    fake           가짜 생성기
    diffusers_stub 로컬 추론(diffusers) 생성기의 뼈대
"""

from .diffusers_stub import DiffusersVideoGenerator
from .fake import FakeVideoGenerator
from .openrouter import OpenRouterVideoGenerator
from .resilience import (
    CachingVideoGenerator,
    DelegatingVideoGenerator,
    RetryingVideoGenerator,
    TimeoutVideoGenerator,
)

__all__ = [
    "CachingVideoGenerator",
    "DelegatingVideoGenerator",
    "DiffusersVideoGenerator",
    "FakeVideoGenerator",
    "OpenRouterVideoGenerator",
    "RetryingVideoGenerator",
    "TimeoutVideoGenerator",
]
