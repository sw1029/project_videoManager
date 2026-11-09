"""파이프라인 단계 사이를 흐르는 가변 컨텍스트."""

from __future__ import annotations

from dataclasses import dataclass, field

from frame.domain.artifacts import GeneratedClip, VideoArtifact
from frame.domain.ir import IntermediateRepresentation
from frame.domain.models import ReferenceImage, VideoVersion
from frame.domain.tracking import TrackRecord

from ..ports.checker import CheckOutcome


@dataclass
class PipelineContext:
    version: VideoVersion
    references: tuple[ReferenceImage, ...] = ()
    parent: VideoVersion | None = None
    clips: dict[str, GeneratedClip] = field(default_factory=dict)
    video: VideoArtifact | None = None
    tracks: list[TrackRecord] = field(default_factory=list)
    check_outcomes: list[CheckOutcome] = field(default_factory=list)
    changed_scene_keys: set[str] = field(default_factory=set)  # 이번 실행에서 새로 생성한 장면
    reused_scene_keys: set[str] = field(default_factory=set)  # 부모 클립을 재사용한 장면
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def ir(self) -> IntermediateRepresentation:
        return self.version.require_ir()
