"""AdapterRegistry (Abstract Factory + Registry): 포트별로 '키 -> 팩토리' 를 등록한다.

새 모델/저장소를 붙이는 절차
1. 포트를 구현한 어댑터 클래스를 adapters/ 에 추가한다.
2. default_registry() 에 registry.register(Port, "키", factory) 한 줄을 추가한다. (또는 플러그인에서 등록)
3. 설정(FRAME_GENERATOR=키 등)만 바꾸면 교체된다. 코드의 다른 곳은 바뀌지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from frame.application.errors import ConfigurationError
from frame.application.ports import (
    ArtifactStore,
    Checker,
    Clock,
    IdentityReferenceExtractor,
    IdGenerator,
    JobDispatcher,
    JobHandlerRegistry,
    ObjectTracker,
    RequirementStructurer,
    UnitOfWork,
    VideoAssembler,
    VideoGenerator,
)
from frame.application.ports.llm import LLMClient, LLMKeySource

from .settings import Settings


@dataclass
class BuildContext:
    """팩토리가 참조할 수 있는 공용 객체. (이미 만들어진 것만 담는다)"""

    settings: Settings
    ids: IdGenerator
    clock: Clock
    artifact_store: ArtifactStore | None = None
    handlers: JobHandlerRegistry | None = None
    llm_keys: LLMKeySource | None = None  # 화면 입력 키를 OpenAI 클라이언트와 설정 API 가 공유


Factory = Callable[[BuildContext], Any]


class AdapterRegistry:
    def __init__(self) -> None:
        self._factories: dict[tuple[str, str], Factory] = {}

    def register(self, port: type, key: str, factory: Factory) -> None:
        self._factories[(port.__name__, key)] = factory

    def build(self, port: type, key: str, ctx: BuildContext) -> Any:
        try:
            factory = self._factories[(port.__name__, key)]
        except KeyError as exc:
            raise ConfigurationError(
                f"{port.__name__} 에 '{key}' 어댑터가 등록되어 있지 않습니다. "
                f"사용 가능: {self.keys(port)}"
            ) from exc
        return factory(ctx)

    def keys(self, port: type) -> list[str]:
        return sorted(k for (p, k) in self._factories if p == port.__name__)


def default_registry() -> AdapterRegistry:
    # 어댑터 모듈은 여기서만 import 한다 (import 부작용 없음, 지연 로딩 가능)
    from frame.adapters.checker import (
        FakeConsistencyChecker,
        FakePreservationChecker,
        FakeRequirementChecker,
        SsimPreservationChecker,
        VlmConsistencyChecker,
    )
    from frame.adapters.generator import (
        DiffusersVideoGenerator,
        FakeVideoAssembler,
        FakeVideoGenerator,
    )
    from frame.adapters.identity import (
        FakeIdentityExtractor,
        FrameIdentityExtractor,
        VlmCropIdentityExtractor,
    )
    from frame.adapters.jobs import (
        InlineJobDispatcher,
        RecordingJobDispatcher,
        ThreadPoolJobDispatcher,
    )
    from frame.adapters.llm import ReplayLLMClient
    from frame.adapters.persistence.memory import InMemoryStore, in_memory_uow_factory
    from frame.adapters.persistence.sqlalchemy import (
        create_engine_from_url,
        create_schema,
        make_session_factory,
        sqlalchemy_uow_factory,
    )
    from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
    from frame.adapters.structurer import FakeStructurer, LLMStructurer, RuleBasedStructurer
    from frame.adapters.tracker import FakeObjectTracker, OpenCvObjectTracker

    reg = AdapterRegistry()

    # --- LLM 클라이언트 (구조화·검수가 공유) ---
    reg.register(LLMClient, "fake", lambda ctx: ReplayLLMClient())

    def _openai(ctx: BuildContext) -> LLMClient:
        from frame.adapters.llm.key_store import LLMKeyStore
        from frame.adapters.llm.openai_client import OpenAIJsonClient  # openai 패키지는 llm extra

        s = ctx.settings
        try:
            return OpenAIJsonClient(
                model=s.llm_model,
                base_url=s.llm_base_url,
                timeout_s=s.llm_timeout_s,
                temperature=s.llm_temperature,
                strict=s.llm_strict_schema,
                key_source=ctx.llm_keys or LLMKeyStore(key_file=s.llm_key_file),
                # 화면 키 입력을 켜면 키 없이 기동하고, 키가 없을 때의 호출만 실패시킨다
                require_key_at_start=not s.llm_runtime_key,
            )
        except ImportError as exc:
            raise ConfigurationError(
                "FRAME_STRUCTURER=llm 에 필요한 openai 패키지가 없습니다. "
                'pip install -e ".[llm]" 로 설치하세요'
            ) from exc

    reg.register(LLMClient, "openai", _openai)

    def _vlm(ctx: BuildContext) -> LLMClient:
        """이미지를 읽는 LLM 클라이언트. 공급자·키·주소는 구조화와 같고 모델만 FRAME_VLM_MODEL 로 바꾼다."""
        s = ctx.settings
        model = s.vlm_model or s.llm_model
        return reg.build(
            LLMClient, s.llm_provider, replace(ctx, settings=replace(s, llm_model=model))
        )

    def _ffmpeg(ctx: BuildContext) -> str:
        from frame.adapters.generator.ffmpeg import resolve_ffmpeg

        return resolve_ffmpeg(ctx.settings.video_ffmpeg)

    # --- 요구사항 구조화 (US-06) ---
    reg.register(RequirementStructurer, "rule_based", lambda ctx: RuleBasedStructurer())
    reg.register(RequirementStructurer, "fake", lambda ctx: FakeStructurer())
    reg.register(
        RequirementStructurer,
        "llm",
        lambda ctx: LLMStructurer(
            reg.build(LLMClient, ctx.settings.llm_provider, ctx),
            max_attempts=ctx.settings.llm_max_attempts,
            timeout_s=ctx.settings.llm_timeout_s,
        ),
    )

    # --- 영상 생성 / 조립 (US-01) ---
    reg.register(VideoGenerator, "fake", lambda ctx: FakeVideoGenerator(_store(ctx)))
    reg.register(VideoGenerator, "diffusers", lambda ctx: DiffusersVideoGenerator())

    def _external_api(ctx: BuildContext) -> VideoGenerator:
        """OpenRouter 영상 API (ADR-0011). 키나 ffmpeg 가 없으면 기동 시점에 ConfigurationError."""
        from frame.adapters.generator.ffmpeg import FfmpegVideoNormalizer, resolve_ffmpeg
        from frame.adapters.generator.openrouter import (
            OpenRouterVideoGenerator,
            resolve_openrouter_key,
        )

        s = ctx.settings
        return OpenRouterVideoGenerator(
            store=_store(ctx),
            api_key=resolve_openrouter_key(key_file=s.video_key_file),
            model=s.video_model,
            resolution=s.video_resolution,
            aspect_ratio=s.video_aspect_ratio,
            fps=s.video_fps,
            base_url=s.video_base_url,
            timeout_seconds=s.video_timeout_s,
            poll_interval_seconds=s.video_poll_interval_s,
            normalizer=FfmpegVideoNormalizer(resolve_ffmpeg(s.video_ffmpeg)),
        )

    reg.register(VideoGenerator, "external_api", _external_api)
    reg.register(VideoAssembler, "fake", lambda ctx: FakeVideoAssembler(_store(ctx)))

    def _ffmpeg_assembler(ctx: BuildContext) -> VideoAssembler:
        """장면 클립을 이어 최종 mp4 를 만든다. 생성기가 정규화한 클립과 같은 규격(크기·fps)을 목표로 한다."""
        from frame.adapters.generator.ffmpeg import FfmpegVideoAssembler, resolve_ffmpeg
        from frame.adapters.generator.openrouter import output_size

        s = ctx.settings
        width, height = output_size(s.video_resolution, s.video_aspect_ratio)
        return FfmpegVideoAssembler(
            _store(ctx), resolve_ffmpeg(s.video_ffmpeg), fps=s.video_fps, width=width, height=height
        )

    reg.register(VideoAssembler, "ffmpeg", _ffmpeg_assembler)

    # --- 정체성 기준 이미지 추출 (US-03, ADR-0015). off 면 추출기가 없다 (기준 이미지를 쓰지 않음) ---
    reg.register(IdentityReferenceExtractor, "off", lambda ctx: None)
    reg.register(IdentityReferenceExtractor, "fake", lambda ctx: FakeIdentityExtractor(_store(ctx)))
    reg.register(
        IdentityReferenceExtractor,
        "frame",
        lambda ctx: FrameIdentityExtractor(_store(ctx), _ffmpeg(ctx)),
    )
    reg.register(
        IdentityReferenceExtractor,
        "vlm_crop",
        lambda ctx: VlmCropIdentityExtractor(_store(ctx), _ffmpeg(ctx), _vlm(ctx)),
    )

    # --- 객체 추적 (US-03) ---
    reg.register(ObjectTracker, "fake", lambda ctx: FakeObjectTracker())
    reg.register(ObjectTracker, "opencv", lambda ctx: OpenCvObjectTracker())

    # --- 검수 (US-03 / US-06) ---
    reg.register(Checker, "fake_requirement", lambda ctx: FakeRequirementChecker())
    reg.register(Checker, "fake_consistency", lambda ctx: FakeConsistencyChecker())
    reg.register(Checker, "fake_preservation", lambda ctx: FakePreservationChecker())
    # 실제 검수기 (ADR-0015, 0016): ffmpeg 로 클립을 읽는다. 일관성은 VLM(FRAME_VLM_MODEL)이 프레임을 판정한다
    reg.register(
        Checker,
        "vlm_consistency",
        lambda ctx: VlmConsistencyChecker(
            _store(ctx),
            _ffmpeg(ctx),
            _vlm(ctx),
            frames_per_scene=ctx.settings.consistency_frames,
            threshold=ctx.settings.consistency_threshold,
        ),
    )
    reg.register(
        Checker,
        "ssim_preservation",
        lambda ctx: SsimPreservationChecker(
            _store(ctx), _ffmpeg(ctx), threshold=ctx.settings.preservation_threshold
        ),
    )

    # --- 산출물 저장 ---
    reg.register(ArtifactStore, "memory", lambda ctx: InMemoryArtifactStore())
    reg.register(
        ArtifactStore, "local_fs", lambda ctx: LocalFileArtifactStore(ctx.settings.artifact_dir)
    )

    # --- 영속성 (UnitOfWork 팩토리를 돌려준다) ---
    reg.register(UnitOfWork, "memory", lambda ctx: in_memory_uow_factory(InMemoryStore()))

    def _sqlalchemy(ctx: BuildContext):  # type: ignore[no-untyped-def]
        engine = create_engine_from_url(ctx.settings.db_url)
        if ctx.settings.db_url.startswith("sqlite") or ctx.settings.db_create_schema:
            create_schema(engine)  # 운영(MySQL)은 Alembic 마이그레이션으로 스키마를 만든다
        return sqlalchemy_uow_factory(make_session_factory(engine))

    reg.register(UnitOfWork, "sqlalchemy", _sqlalchemy)

    # --- 작업 디스패처 ---
    reg.register(JobDispatcher, "inline", lambda ctx: InlineJobDispatcher(_handlers(ctx)))
    reg.register(JobDispatcher, "recording", lambda ctx: RecordingJobDispatcher(_handlers(ctx)))
    reg.register(JobDispatcher, "thread", lambda ctx: ThreadPoolJobDispatcher(_handlers(ctx)))
    return reg


def _store(ctx: BuildContext) -> ArtifactStore:
    if ctx.artifact_store is None:
        raise ConfigurationError("artifact_store 가 아직 만들어지지 않았습니다")
    return ctx.artifact_store


def _handlers(ctx: BuildContext) -> JobHandlerRegistry:
    if ctx.handlers is None:
        raise ConfigurationError("handlers 가 아직 만들어지지 않았습니다")
    return ctx.handlers
