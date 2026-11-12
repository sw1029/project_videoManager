"""Container: 설정과 레지스트리로 객체 그래프를 조립한다 (Dependency Injection 의 수동 구성).

overrides 로 어떤 구성 요소든 직접 주입할 수 있어, 테스트는 '실패하는 생성기' 같은 특수 더블을
설정 키 없이도 꽂을 수 있다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from frame.adapters.common import SystemClock, UuidIdGenerator
from frame.adapters.generator.decorators import (
    CachingVideoGenerator,
    RetryingVideoGenerator,
    TimeoutVideoGenerator,
    pause_before_retry,
)
from frame.application.checks import CheckSuite
from frame.application.jobs import RUN_GENERATION_PIPELINE, STRUCTURE_REQUIREMENTS
from frame.application.pipeline import (
    CheckStage,
    GenerateStage,
    PipelineObserver,
    PipelineRunner,
    TrackStage,
)
from frame.application.ports import (
    ArtifactStore,
    Checker,
    Clock,
    IdGenerator,
    JobDispatcher,
    JobHandlerRegistry,
    ObjectTracker,
    RequirementStructurer,
    UnitOfWork,
    UnitOfWorkFactory,
    VideoAssembler,
    VideoGenerator,
)
from frame.application.use_cases import (
    AcceptGeneration,
    CreateProject,
    CreateRevision,
    ProjectQueries,
    RunGenerationPipeline,
    StructureRequirements,
)
from frame.domain.services.propagation import ChangePropagationPolicy, DefaultPropagationPolicy

from .registry import AdapterRegistry, BuildContext, default_registry
from .settings import Settings

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    ids: IdGenerator
    clock: Clock
    artifact_store: ArtifactStore
    structurer: RequirementStructurer
    generator: VideoGenerator
    assembler: VideoAssembler
    tracker: ObjectTracker
    checkers: tuple[Checker, ...]
    uow_factory: UnitOfWorkFactory
    handlers: JobHandlerRegistry
    dispatcher: JobDispatcher
    propagation_policy: ChangePropagationPolicy
    create_project: CreateProject
    structure_requirements: StructureRequirements
    create_revision: CreateRevision
    accept_generation: AcceptGeneration
    run_generation_pipeline: RunGenerationPipeline
    queries: ProjectQueries


def _warn_if_timeout_cuts_before_adapter(settings: Settings) -> None:
    """범용 시간 제한은 OpenRouter 어댑터의 자체 제한(폴링)에 제출·다운로드·정규화를 더한 시간보다 길어야 한다.

    짧으면 범용 제한이 먼저 끊어 job id 없는 오류가 남고, 버려진 스레드는 어댑터 제한까지 폴링을 계속한다 (ADR-0012).
    """
    if settings.generator == "external_api" and (
        settings.generator_timeout_s <= settings.video_timeout_s
    ):
        log.warning(
            "FRAME_GENERATOR_TIMEOUT_S(%gs) 가 FRAME_VIDEO_TIMEOUT_S(%gs) 보다 길지 않습니다. "
            "어댑터가 job id 를 담아 GENERATION_TIMEOUT 을 내기 전에 범용 시간 제한이 먼저 끊습니다 "
            "(권장: FRAME_VIDEO_TIMEOUT_S + 300초 이상)",
            settings.generator_timeout_s,
            settings.video_timeout_s,
        )


def build_container(
    settings: Settings | None = None,
    registry: AdapterRegistry | None = None,
    overrides: Mapping[str, Any] | None = None,
    observers: tuple[PipelineObserver, ...] = (),
) -> Container:
    settings = settings or Settings()
    registry = registry or default_registry()
    ov: dict[str, Any] = dict(overrides or {})

    def pick(name: str, build: Any) -> Any:
        return ov[name] if name in ov else build()

    ids: IdGenerator = pick("ids", UuidIdGenerator)
    clock: Clock = pick("clock", SystemClock)
    ctx = BuildContext(settings=settings, ids=ids, clock=clock)
    ctx.artifact_store = pick(
        "artifact_store", lambda: registry.build(ArtifactStore, settings.artifact_store, ctx)
    )
    structurer = pick(
        "structurer", lambda: registry.build(RequirementStructurer, settings.structurer, ctx)
    )
    generator: VideoGenerator = pick(
        "generator", lambda: registry.build(VideoGenerator, settings.generator, ctx)
    )
    # Decorator 체인은 설정으로 켠다. 안쪽 -> 바깥: Retry -> Timeout -> Cache (ADR-0012)
    # 캐시 적중은 시간 제한을 거치지 않고, 시간 초과는 Retry 바깥에서 나므로 재시도되지 않는다.
    if settings.generator_retries > 0:
        generator = RetryingVideoGenerator(
            generator, retries=settings.generator_retries, sleep=pause_before_retry
        )
    if settings.generator_timeout_s > 0:
        _warn_if_timeout_cuts_before_adapter(settings)
        generator = TimeoutVideoGenerator(generator, settings.generator_timeout_s)
    if settings.generator_cache:
        generator = CachingVideoGenerator(generator)
    assembler = pick("assembler", lambda: registry.build(VideoAssembler, settings.assembler, ctx))
    tracker = pick("tracker", lambda: registry.build(ObjectTracker, settings.tracker, ctx))
    checkers: tuple[Checker, ...] = pick(
        "checkers", lambda: tuple(registry.build(Checker, key, ctx) for key in settings.checkers)
    )
    uow_factory: UnitOfWorkFactory = pick(
        "uow_factory", lambda: registry.build(UnitOfWork, settings.persistence, ctx)
    )
    policy: ChangePropagationPolicy = pick("propagation_policy", DefaultPropagationPolicy)

    # 작업 핸들러 <-> 디스패처 <-> 유스케이스 순환은 레지스트리를 먼저 만들어 끊는다
    handlers = JobHandlerRegistry()
    ctx.handlers = handlers
    dispatcher: JobDispatcher = pick(
        "dispatcher", lambda: registry.build(JobDispatcher, settings.jobs, ctx)
    )

    stages = (
        GenerateStage(generator, assembler),
        TrackStage(tracker),
        CheckStage(CheckSuite(checkers)),
    )
    structure_requirements = StructureRequirements(uow_factory, structurer)
    run_generation_pipeline = RunGenerationPipeline(
        uow_factory, stages, PipelineRunner(observers), ids
    )
    handlers.register(
        STRUCTURE_REQUIREMENTS,
        lambda job: structure_requirements.execute(job.payload["version_id"]),
    )
    handlers.register(
        RUN_GENERATION_PIPELINE,
        lambda job: run_generation_pipeline.execute(job.payload["version_id"]),
    )

    return Container(
        settings=settings,
        ids=ids,
        clock=clock,
        artifact_store=ctx.artifact_store,
        structurer=structurer,
        generator=generator,
        assembler=assembler,
        tracker=tracker,
        checkers=checkers,
        uow_factory=uow_factory,
        handlers=handlers,
        dispatcher=dispatcher,
        propagation_policy=policy,
        create_project=CreateProject(uow_factory, dispatcher, ids, clock),
        structure_requirements=structure_requirements,
        create_revision=CreateRevision(uow_factory, ids, clock, policy),
        accept_generation=AcceptGeneration(uow_factory, dispatcher),
        run_generation_pipeline=run_generation_pipeline,
        queries=ProjectQueries(uow_factory),
    )
