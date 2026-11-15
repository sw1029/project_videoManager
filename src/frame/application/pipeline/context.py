"""파이프라인 단계 사이를 흐르는 가변 컨텍스트."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from frame.domain.artifacts import GeneratedClip, VideoArtifact
from frame.domain.checks import CheckResult
from frame.domain.ir import IntermediateRepresentation
from frame.domain.models import ReferenceImage, VideoVersion
from frame.domain.progress import PipelineProgress
from frame.domain.tracking import TrackRecord

from ..ports.checker import CheckOutcome

# (끝낸 항목 수, 전체 항목 수, 지금 처리할 항목) -> None
ProgressReporter = Callable[[int, int, str | None], None]


@dataclass
class PipelineContext:
    version: VideoVersion
    # 이 버전의 참조 이미지 (사용자 참조 + 정체성 기준). 생성 단계가 새 기준을 뽑으면 여기에 더한다
    references: tuple[ReferenceImage, ...] = ()
    parent: VideoVersion | None = None
    # 부모 버전의 검수 결과 (다시 만들지 않은 장면의 판정을 물려받는 데 쓴다, ADR-0018)
    parent_checks: tuple[CheckResult, ...] = ()
    clips: dict[str, GeneratedClip] = field(default_factory=dict)
    video: VideoArtifact | None = None
    tracks: list[TrackRecord] = field(default_factory=list)
    check_outcomes: list[CheckOutcome] = field(default_factory=list)
    changed_scene_keys: set[str] = field(default_factory=set)  # 이번 실행에서 새로 생성한 장면
    reused_scene_keys: set[str] = field(default_factory=set)  # 부모 클립을 재사용한 장면
    # 이번 실행에서 새로 뽑은 정체성 기준 이미지. 유스케이스가 클립과 함께 저장한다 (ADR-0015)
    derived_references: list[ReferenceImage] = field(default_factory=list)
    # 단계별 소요 시간 (실패한 단계 포함)
    timings_ms: dict[str, float] = field(default_factory=dict)
    # 단계·장면 진행. 처리 도중과 끝에 버전에 저장된다 (GET /api/versions/{id} 의 progress)
    progress: PipelineProgress = field(default_factory=PipelineProgress)
    _reporter: ProgressReporter | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def ir(self) -> IntermediateRepresentation:
        return self.version.require_ir()

    def add_reference(self, reference: ReferenceImage) -> None:
        """새로 뽑은 정체성 기준 이미지를 이 실행의 참조 목록에 더한다."""
        self.references = (*self.references, reference)
        self.derived_references.append(reference)

    def report_progress(self, done: int, total: int, current: str | None = None) -> None:
        """실행 중인 단계가 항목(장면) 단위 진행을 알린다: 끝낸 수, 전체 수, 지금 처리할 항목.

        PipelineStage.run() 안에서만 관찰자에게 전달된다 (on_stage_progress). 그 밖에서는 아무 일도 하지 않는다.
        """
        if self._reporter is not None:
            self._reporter(done, total, current)

    @contextmanager
    def reporting_to(self, reporter: ProgressReporter) -> Iterator[None]:
        """단계 하나를 실행하는 동안 report_progress 를 reporter 로 보낸다 (PipelineStage.run 이 쓴다)."""
        previous, self._reporter = self._reporter, reporter
        try:
            yield
        finally:
            self._reporter = previous
