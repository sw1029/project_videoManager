"""생성 -> 추적 -> 검수 파이프라인 (Template Method + Chain).

맡는 개념
    접수된 버전 하나를 단계를 따라 처리한다. 단계는 같은 맥락(PipelineContext)에 결과를 쌓고, 실행기가 단계마다의
    시간과 실패를 정리한다. 버전의 상태를 바꾸고 저장하는 일은 유스케이스(run_generation_pipeline)가 한다.

밖에서 쓰는 이름
    PipelineRunner · PipelineFailure          단계를 차례로 돌리는 실행기와, 실패를 정리한 값
    PipelineStage · PipelineObserver          단계와 관찰자의 틀
    GenerateStage · TrackStage · CheckStage   구체 단계
    PipelineContext                           단계들이 함께 쓰는 맥락
    LoggingObserver                           단계의 시작·끝을 로그로 남기는 관찰자

모듈 지도
    stage                 단계와 관찰자의 틀 (Template Method)
    stages                구체 단계: 생성(재사용할 것 고르기 -> 생성 -> 조립), 추적, 검수
    identity_anchoring    생성 단계의 협력 객체: 정체성 기준 이미지를 뽑고 고른다 (ADR-0015)
    requests              생성 요청 만들기: 조건 요청, 부모 클립, 수정 지시
    context               맥락과 진행 보고
    runner · observers    실행기, 관찰자
"""

from .context import PipelineContext
from .observers import LoggingObserver
from .runner import PipelineFailure, PipelineRunner
from .stage import PipelineObserver, PipelineStage
from .stages import CheckStage, GenerateStage, TrackStage

__all__ = [
    "CheckStage",
    "GenerateStage",
    "LoggingObserver",
    "PipelineContext",
    "PipelineFailure",
    "PipelineObserver",
    "PipelineRunner",
    "PipelineStage",
    "TrackStage",
]
