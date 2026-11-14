"""포트(Port) = 애플리케이션이 바깥 세계에 요구하는 인터페이스.

교체 가능한 요소는 전부 여기의 추상 클래스로만 참조한다.
"""

from .artifact_store import ArtifactStore
from .checker import CheckContext, Checker, CheckOutcome
from .common import Clock, IdGenerator
from .generator import (
    GeneratedClip,
    ParentClip,
    SceneGenerationRequest,
    VideoArtifact,
    VideoAssembler,
    VideoGenerator,
)
from .identity import IdentityExtractionRequest, IdentityImage, IdentityReferenceExtractor
from .jobs import Job, JobDispatcher, JobHandler, JobHandlerRegistry
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
    "IrDocumentStructurer",
    "Job",
    "JobDispatcher",
    "JobHandler",
    "JobHandlerRegistry",
    "ObjectTracker",
    "ParentClip",
    "ProjectRepository",
    "ReferenceImageInput",
    "ReferenceImageRepository",
    "RequirementStructurer",
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
