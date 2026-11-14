"""팩토리들이 함께 기대는 것: 먼저 만들어진 객체, ffmpeg 경로, 글·이미지를 읽는 LLM 클라이언트.

팩토리는 조립 맥락(BuildContext) 하나만 받는다. 거기서 필요한 것을 꺼내는 방법을 포트 모듈마다 다시 쓰지 않게 모았다.
"""

from __future__ import annotations

from dataclasses import replace

from frame.adapters.generator.ffmpeg import resolve_ffmpeg
from frame.application.errors import ConfigurationError
from frame.application.ports import ArtifactStore, JobHandlerRegistry
from frame.application.ports.llm import LLMClient

from ..registry import AdapterRegistry, BuildContext


def artifact_store(ctx: BuildContext) -> ArtifactStore:
    """컨테이너가 먼저 만들어 맥락에 넣어 둔 산출물 저장소."""
    if ctx.artifact_store is None:
        raise ConfigurationError("artifact_store 가 아직 만들어지지 않았습니다")
    return ctx.artifact_store


def job_handlers(ctx: BuildContext) -> JobHandlerRegistry:
    """컨테이너가 먼저 만들어 맥락에 넣어 둔 작업 핸들러 표."""
    if ctx.handlers is None:
        raise ConfigurationError("handlers 가 아직 만들어지지 않았습니다")
    return ctx.handlers


def ffmpeg_path(ctx: BuildContext) -> str:
    """ffmpeg 실행 파일. 찾지 못하면 ConfigurationError 라서, 이것을 쓰는 어댑터는 기동 시점에 실패한다."""
    return resolve_ffmpeg(ctx.settings.video_ffmpeg)


def llm_client(registry: AdapterRegistry, ctx: BuildContext) -> LLMClient:
    """글을 읽는 LLM 클라이언트. 공급자는 FRAME_LLM_PROVIDER, 모델은 FRAME_LLM_MODEL."""
    client: LLMClient = registry.build(LLMClient, ctx.settings.llm_provider, ctx)
    return client


def vlm_client(registry: AdapterRegistry, ctx: BuildContext) -> LLMClient:
    """이미지를 읽는 LLM 클라이언트. 공급자·키·주소는 구조화와 같고 모델만 FRAME_VLM_MODEL 로 바꾼다."""
    settings = ctx.settings
    model = settings.vlm_model or settings.llm_model
    return llm_client(registry, replace(ctx, settings=replace(settings, llm_model=model)))
