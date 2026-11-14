"""Container: 설정과 레지스트리로 객체 그래프를 조립한다 (Dependency Injection 의 수동 구성).

overrides 로 어떤 구성 요소든 직접 주입할 수 있어, 테스트는 '실패하는 생성기' 같은 특수 더블을
설정 키 없이도 꽂을 수 있다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from frame.adapters.generator.cache import ClipCache, build_clip_cache
from frame.adapters.generator.decorators import (
    CachingVideoGenerator,
    RetryingVideoGenerator,
    TimeoutVideoGenerator,
    pause_before_retry,
)
from frame.adapters.llm.key_store import LLMKeyStore
from frame.adapters.shared.runtime import SystemClock, UuidIdGenerator
from frame.adapters.structurer.passthrough import PassthroughStructurer
from frame.application.checks import CheckSuite
from frame.application.errors import ConfigurationError
from frame.application.jobs import RUN_GENERATION_PIPELINE, STRUCTURE_REQUIREMENTS
from frame.application.pipeline import (
    CheckStage,
    GenerateStage,
    LoggingObserver,
    PipelineObserver,
    PipelineRunner,
    TrackStage,
)
from frame.application.ports import (
    ArtifactStore,
    Checker,
    Clock,
    IdentityReferenceExtractor,
    IdGenerator,
    IrDocumentStructurer,
    JobDispatcher,
    JobHandlerRegistry,
    ObjectTracker,
    RequirementStructurer,
    UnitOfWork,
    UnitOfWorkFactory,
    VideoAssembler,
    VideoGenerator,
)
from frame.application.ports.change_request import ChangeRequestInterpreter
from frame.application.ports.llm import LLMKeySource
from frame.application.use_cases import (
    AcceptGeneration,
    CreateProject,
    CreateRevision,
    EditDraftIr,
    PlanRevision,
    ProjectQueries,
    RecoverPendingVersions,
    RunGenerationPipeline,
    StructureRequirements,
    UploadReferenceImage,
)
from frame.domain.enums import EntityKind
from frame.domain.identity import IdentityPolicy
from frame.domain.revision import ChangePropagationPolicy, DefaultPropagationPolicy

from .registry import AdapterRegistry, BuildContext, default_registry
from .settings import Settings
from .theme_wiring import ThemeServices, build_theme_services

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    ids: IdGenerator
    clock: Clock
    artifact_store: ArtifactStore
    structurer: RequirementStructurer
    # 화면이 조립한 IR 문서의 보정·검증 (IR 직접 입력·DRAFT IR 수정, #10 #11)
    ir_structurer: IrDocumentStructurer
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
    edit_draft_ir: EditDraftIr  # DRAFT IR 수정 (#11)
    plan_revision: PlanRevision  # 변경 계획 미리보기 (ADR-0019)
    accept_generation: AcceptGeneration
    run_generation_pipeline: RunGenerationPipeline
    queries: ProjectQueries
    upload_reference_image: UploadReferenceImage  # 참조 이미지 업로드 (#12)
    # 재시작 복구 (#2): 서버 기동 훅과 주기 검사가 부른다
    recover_pending_versions: RecoverPendingVersions
    llm_keys: LLMKeySource | None = None  # FRAME_STRUCTURER=llm + openai 일 때만
    # 정체성 기준 이미지 추출기 (ADR-0015). FRAME_IDENTITY=off 면 None
    identity_extractor: IdentityReferenceExtractor | None = None
    # 테마 추출·시리즈 편·녹음 붙이기·화면 국소 수정 (ADR-0025~0027). 조립은 config/theme_wiring.py
    series: ThemeServices | None = None


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


def change_interpreter_key(settings: Settings) -> str:
    """변경 요청 해석기의 종류. auto 는 구조화기를 따른다 (LLM 구조화면 llm, 아니면 rule_based)."""
    if settings.change_interpreter != "auto":
        return settings.change_interpreter
    return "llm" if settings.structurer == "llm" else "rule_based"


def _identity_policy(settings: Settings) -> IdentityPolicy:
    try:
        kinds = tuple(EntityKind(kind) for kind in settings.identity_kinds)
    except ValueError as exc:
        raise ConfigurationError(
            f"FRAME_IDENTITY_KINDS 에 알 수 없는 객체 종류가 있습니다: {settings.identity_kinds} "
            f"(사용 가능: {', '.join(EntityKind)})"
        ) from exc
    return IdentityPolicy(kinds=kinds, max_references=max(0, settings.identity_max_references))


def build_container(
    settings: Settings | None = None,
    registry: AdapterRegistry | None = None,
    overrides: Mapping[str, Any] | None = None,
    observers: tuple[PipelineObserver, ...] | None = None,
) -> Container:
    """observers 를 주지 않으면 LoggingObserver 하나를 붙인다 (주면 그것만 쓴다)."""
    settings = settings or Settings()
    observers = (LoggingObserver(),) if observers is None else tuple(observers)
    registry = registry or default_registry()
    ov: dict[str, Any] = dict(overrides or {})

    def pick(name: str, build: Any) -> Any:
        return ov[name] if name in ov else build()

    ids: IdGenerator = pick("ids", UuidIdGenerator)
    clock: Clock = pick("clock", SystemClock)
    ctx = BuildContext(settings=settings, ids=ids, clock=clock)
    uses_openai = settings.structurer == "llm" and settings.llm_provider == "openai"
    ctx.llm_keys = pick(
        "llm_keys", lambda: LLMKeyStore(key_file=settings.llm_key_file) if uses_openai else None
    )
    ctx.artifact_store = pick(
        "artifact_store", lambda: registry.build(ArtifactStore, settings.artifact_store, ctx)
    )
    structurer = pick(
        "structurer", lambda: registry.build(RequirementStructurer, settings.structurer, ctx)
    )
    ir_structurer: IrDocumentStructurer = pick("ir_structurer", PassthroughStructurer)
    generator: VideoGenerator = pick(
        "generator", lambda: registry.build(VideoGenerator, settings.generator, ctx)
    )
    # 생성 결과 캐시 (#7, ADR-0013): 키에 넣을 프로필은 데코레이터로 감싸기 전의 생성기에서 받는다
    clip_cache: ClipCache = pick(
        "clip_cache", lambda: build_clip_cache(ctx.artifact_store, generator.profile())
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
        generator = CachingVideoGenerator(generator, cache=clip_cache)
    assembler = pick("assembler", lambda: registry.build(VideoAssembler, settings.assembler, ctx))
    identity_extractor: IdentityReferenceExtractor | None = pick(
        "identity_extractor",
        lambda: registry.build(IdentityReferenceExtractor, settings.identity, ctx),
    )
    tracker = pick("tracker", lambda: registry.build(ObjectTracker, settings.tracker, ctx))
    checkers: tuple[Checker, ...] = pick(
        "checkers", lambda: tuple(registry.build(Checker, key, ctx) for key in settings.checkers)
    )
    uow_factory: UnitOfWorkFactory = pick(
        "uow_factory", lambda: registry.build(UnitOfWork, settings.persistence, ctx)
    )
    policy: ChangePropagationPolicy = pick("propagation_policy", DefaultPropagationPolicy)
    change_interpreter: ChangeRequestInterpreter = pick(
        "change_interpreter",
        lambda: registry.build(ChangeRequestInterpreter, change_interpreter_key(settings), ctx),
    )

    # 작업 핸들러 <-> 디스패처 <-> 유스케이스 순환은 레지스트리를 먼저 만들어 끊는다
    handlers = JobHandlerRegistry()
    ctx.handlers = handlers
    dispatcher: JobDispatcher = pick(
        "dispatcher", lambda: registry.build(JobDispatcher, settings.jobs, ctx)
    )

    stages = (
        GenerateStage(
            generator,
            assembler,
            identity=identity_extractor,
            identity_policy=_identity_policy(settings),
            ids=ids,
        ),
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

    container = Container(
        settings=settings,
        ids=ids,
        clock=clock,
        artifact_store=ctx.artifact_store,
        structurer=structurer,
        ir_structurer=ir_structurer,
        generator=generator,
        assembler=assembler,
        tracker=tracker,
        checkers=checkers,
        uow_factory=uow_factory,
        handlers=handlers,
        dispatcher=dispatcher,
        propagation_policy=policy,
        create_project=CreateProject(
            uow_factory,
            dispatcher,
            ids,
            clock,
            ir_structurer=ir_structurer,
            artifact_store=ctx.artifact_store,
        ),
        structure_requirements=structure_requirements,
        create_revision=CreateRevision(uow_factory, ids, clock, policy),
        edit_draft_ir=EditDraftIr(uow_factory, ir_structurer),
        plan_revision=PlanRevision(uow_factory, policy, change_interpreter, generator),
        accept_generation=AcceptGeneration(uow_factory, dispatcher, clock),
        run_generation_pipeline=run_generation_pipeline,
        queries=ProjectQueries(uow_factory),
        upload_reference_image=UploadReferenceImage(
            ctx.artifact_store, ids, max_bytes=settings.reference_image_max_bytes
        ),
        llm_keys=ctx.llm_keys,
        identity_extractor=identity_extractor,
        recover_pending_versions=RecoverPendingVersions(
            uow_factory,
            dispatcher,
            clock,
            lease_seconds=settings.worker_lease_s,
            max_attempts=settings.worker_max_attempts,
        ),
    )
    container.series = build_theme_services(
        settings=settings,
        registry=registry,
        ctx=ctx,
        uow_factory=uow_factory,
        assembler=assembler,
        create_revision=container.create_revision,
        accept_generation=container.accept_generation,
        overrides=ov,
    )
    return container
