"""Container: 설정과 레지스트리로 객체 그래프를 조립한다 (Dependency Injection 의 수동 구성).

overrides 로 어떤 구성 요소든 직접 주입할 수 있어, 테스트는 '실패하는 생성기' 같은 특수 더블을
설정 키 없이도 꽂을 수 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from frame.adapters.common import SystemClock, UuidIdGenerator
from frame.adapters.generator.cache import ClipCache, build_clip_cache
from frame.adapters.generator.decorators import CachingVideoGenerator, RetryingVideoGenerator
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
    RecoverPendingVersions,
    RunGenerationPipeline,
    StructureRequirements,
)
from frame.domain.services.propagation import ChangePropagationPolicy, DefaultPropagationPolicy

from .registry import AdapterRegistry, BuildContext, default_registry
from .settings import Settings


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
    # 재시작 복구 (#2): 서버 기동 훅과 주기 검사가 부른다
    recover_pending_versions: RecoverPendingVersions


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
    # 생성 결과 캐시 (#7, ADR-0013): 키에 넣을 프로필은 데코레이터로 감싸기 전의 생성기에서 받는다
    clip_cache: ClipCache = pick(
        "clip_cache", lambda: build_clip_cache(ctx.artifact_store, generator.profile())
    )
    if settings.generator_retries > 0:  # Decorator 체인은 설정으로 켠다
        generator = RetryingVideoGenerator(generator, retries=settings.generator_retries)
    if settings.generator_cache:
        generator = CachingVideoGenerator(generator, cache=clip_cache)
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
        uow_factory, stages, PipelineRunner(observers), ids, clock=clock
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
        accept_generation=AcceptGeneration(uow_factory, dispatcher, clock),
        run_generation_pipeline=run_generation_pipeline,
        queries=ProjectQueries(uow_factory),
        recover_pending_versions=RecoverPendingVersions(
            uow_factory,
            dispatcher,
            clock,
            lease_seconds=settings.worker_lease_s,
            max_attempts=settings.worker_max_attempts,
        ),
    )
