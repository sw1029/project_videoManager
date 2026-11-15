"""포트(Port) = 애플리케이션이 바깥 세계에 요구하는 인터페이스.

맡는 개념
    교체 가능한 요소는 전부 여기의 추상 클래스로만 참조한다. 구현은 frame.adapters 에 있고,
    어느 구현을 쓸지는 조립(frame.config)이 정한다.

밖에서 쓰는 이름
    입구에서 가져오는 것 (__all__) - 구조화 · 생성 · 조립 · 정체성 기준 · 추적 · 검수 · 변경 요청 해석 · LLM ·
    산출물 저장 · 저장소와 저장 단위 · 작업 · ID 와 시계의 포트와 그 요청 · 결과 타입.
    모듈에서 가져오는 것 - 음성 인식 speech (SpeechRecognizer), 소리 읽기 sound (SoundReader),
    테마·시리즈 기능의 포트 series/ (그 패키지의 입구를 본다).

모듈 지도 - 모듈 하나가 포트 하나다
    structurer        RequirementStructurer · IrDocumentStructurer    요구사항 -> 중간표현
    generator         VideoGenerator                                  장면 클립 생성 (요청 · 부모 클립 · 미리보기)
    assembler         VideoAssembler                                  장면 클립 -> 최종 영상
    identity          IdentityReferenceExtractor                      정체성 기준 이미지 뽑기
    tracker           ObjectTracker                                   객체 추적
    checker           Checker                                         검수
    change_request    ChangeRequestInterpreter                        글로 쓴 변경 요청의 해석
    llm               LLMClient · LLMKeySource                        글 · 이미지를 읽는 모델과 그 키
    artifact_store    ArtifactStore                                   산출물 저장
    repositories · unit_of_work                                       저장소와 저장 단위
    jobs              JobDispatcher · JobHandlerRegistry              작업 실행
    common            Clock · IdGenerator                             시계와 ID
    speech · sound    SpeechRecognizer · SoundReader                  음성 인식, 소리 읽기
    series/           테마·시리즈 기능의 포트
"""

from .artifact_store import ArtifactStore
from .assembler import VideoAssembler
from .change_request import (
    ChangeRequestInterpretation,
    ChangeRequestInterpreter,
    ChangeRequestNotUnderstood,
)
from .checker import CheckContext, Checker, CheckOutcome
from .common import Clock, IdGenerator
from .generator import (
    GeneratedClip,
    ParentClip,
    ProductionMethod,
    ProductionPreview,
    RevisionDirective,
    SceneGenerationRequest,
    VideoArtifact,
    VideoGenerator,
)
from .identity import IdentityExtractionRequest, IdentityImage, IdentityReferenceExtractor
from .jobs import Job, JobDispatcher, JobHandler, JobHandlerRegistry
from .llm import ImageInput, JsonCompletion, LLMClient, LLMKeySource, LLMKeyStatus
from .repositories import (
    CheckResultRepository,
    ProjectRepository,
    ReferenceImageRepository,
    TrackRepository,
    VersionRepository,
)
from .structurer import (
    IrDocumentStructurer,
    ReferenceImageInput,
    RequirementStructurer,
    StructuredDocument,
    StructuringRequest,
)
from .tracker import ObjectTracker, TrackingRequest
from .unit_of_work import UnitOfWork, UnitOfWorkFactory

__all__ = [
    "ArtifactStore",
    "ChangeRequestInterpretation",
    "ChangeRequestInterpreter",
    "ChangeRequestNotUnderstood",
    "CheckContext",
    "CheckOutcome",
    "CheckResultRepository",
    "Checker",
    "Clock",
    "GeneratedClip",
    "IdGenerator",
    "IdentityExtractionRequest",
    "IdentityImage",
    "IdentityReferenceExtractor",
    "ImageInput",
    "IrDocumentStructurer",
    "Job",
    "JobDispatcher",
    "JobHandler",
    "JobHandlerRegistry",
    "JsonCompletion",
    "LLMClient",
    "LLMKeySource",
    "LLMKeyStatus",
    "ObjectTracker",
    "ParentClip",
    "ProductionMethod",
    "ProductionPreview",
    "ProjectRepository",
    "ReferenceImageInput",
    "ReferenceImageRepository",
    "RequirementStructurer",
    "RevisionDirective",
    "SceneGenerationRequest",
    "StructuredDocument",
    "StructuringRequest",
    "TrackRepository",
    "TrackingRequest",
    "UnitOfWork",
    "UnitOfWorkFactory",
    "VersionRepository",
    "VideoArtifact",
    "VideoAssembler",
    "VideoGenerator",
]
