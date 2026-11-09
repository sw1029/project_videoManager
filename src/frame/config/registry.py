"""AdapterRegistry (Abstract Factory + Registry): 포트별로 '키 -> 팩토리' 를 등록한다.

새 모델/저장소를 붙이는 절차
1. 포트를 구현한 어댑터 클래스를 adapters/ 에 추가한다.
2. default_registry() 에 registry.register(Port, "키", factory) 한 줄을 추가한다. (또는 플러그인에서 등록)
3. 설정(FRAME_GENERATOR=키 등)만 바꾸면 교체된다. 코드의 다른 곳은 바뀌지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from frame.application.errors import ConfigurationError
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
    VideoAssembler,
    VideoGenerator,
)
from frame.application.ports.llm import LLMClient

from .settings import Settings


@dataclass
class BuildContext:
    """팩토리가 참조할 수 있는 공용 객체. (이미 만들어진 것만 담는다)"""

    settings: Settings
    ids: IdGenerator
    clock: Clock
    artifact_store: ArtifactStore | None = None
    handlers: JobHandlerRegistry | None = None


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
    )
    from frame.adapters.generator import (
        DiffusersVideoGenerator,
        ExternalApiVideoGenerator,
        FakeVideoAssembler,
        FakeVideoGenerator,
        FfmpegVideoAssembler,
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
        from frame.adapters.llm.openai_client import OpenAIJsonClient  # openai 패키지는 llm extra

        s = ctx.settings
        try:
            return OpenAIJsonClient(
                model=s.llm_model,
                key_file=s.llm_key_file,
                base_url=s.llm_base_url,
                timeout_s=s.llm_timeout_s,
                temperature=s.llm_temperature,
                strict=s.llm_strict_schema,
            )
        except ImportError as exc:
            raise ConfigurationError(
                "FRAME_STRUCTURER=llm 에 필요한 openai 패키지가 없습니다. "
                'pip install -e ".[llm]" 로 설치하세요'
            ) from exc

    reg.register(LLMClient, "openai", _openai)

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
    reg.register(VideoGenerator, "external_api", lambda ctx: ExternalApiVideoGenerator())
    reg.register(VideoAssembler, "fake", lambda ctx: FakeVideoAssembler(_store(ctx)))
    reg.register(VideoAssembler, "ffmpeg", lambda ctx: FfmpegVideoAssembler())

    # --- 객체 추적 (US-03) ---
    reg.register(ObjectTracker, "fake", lambda ctx: FakeObjectTracker())
    reg.register(ObjectTracker, "opencv", lambda ctx: OpenCvObjectTracker())

    # --- 검수 (US-03 / US-06) ---
    reg.register(Checker, "fake_requirement", lambda ctx: FakeRequirementChecker())
    reg.register(Checker, "fake_consistency", lambda ctx: FakeConsistencyChecker())
    reg.register(Checker, "fake_preservation", lambda ctx: FakePreservationChecker())

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
