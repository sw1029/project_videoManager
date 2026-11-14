"""팩토리들이 함께 기대는 것: 먼저 만들어진 객체, ffmpeg 경로, 글·이미지를 읽는 LLM 클라이언트와 그 주소.

팩토리는 조립 맥락(BuildContext) 하나만 받는다. 거기서 필요한 것을 꺼내는 방법을 포트 모듈마다 다시 쓰지 않게 모았다.
구현이 아직 없는 뼈대 어댑터를 거절하는 팩토리(skeleton)도 여기에 있다.
"""

from __future__ import annotations

from dataclasses import replace

from frame.adapters.shared.media.ffmpeg import resolve_ffmpeg
from frame.application.errors import ConfigurationError
from frame.application.ports import ArtifactStore, JobHandlerRegistry
from frame.application.ports.llm import LLMClient

from ..registry import AdapterRegistry, BuildContext, Factory
from ..theme_settings import ThemeSettings

OPENAI_BASE_URL = "https://api.openai.com/v1"  # LLM 주소를 주지 않았을 때 요청이 가는 곳


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


def theme_settings(ctx: BuildContext) -> ThemeSettings:
    """조립이 맥락에 넣어 둔 테마·시리즈 기능의 설정."""
    if ctx.theme_settings is None:
        raise ConfigurationError("theme_settings 가 아직 만들어지지 않았습니다")
    return ctx.theme_settings


def ffmpeg_path(ctx: BuildContext) -> str:
    """ffmpeg 실행 파일. 찾지 못하면 ConfigurationError 라서, 이것을 쓰는 어댑터는 기동 시점에 실패한다."""
    return resolve_ffmpeg(ctx.settings.video_ffmpeg)


def skeleton(setting: str, key: str, *, working: str) -> Factory:
    """구현이 아직 없는 뼈대 어댑터의 자리. 고르면 조립할 때 ConfigurationError 로 거절한다 (서버가 뜨지 않는다).

    뼈대 어댑터는 부르면 언제나 실패한다. 조립에서 거절하지 않으면 유료 생성이 끝난 뒤의 단계에서야 실패한다.
    setting 은 설정 변수 이름, working 은 지금 쓸 수 있는 값이다.
    """

    def refuse(ctx: BuildContext) -> None:
        raise ConfigurationError(
            f"{setting}={key} 는 아직 구현되지 않은 뼈대 어댑터라서 쓸 수 없습니다. "
            f"지금 쓸 수 있는 값: {working}"
        )

    return refuse


def llm_client(registry: AdapterRegistry, ctx: BuildContext) -> LLMClient:
    """글을 읽는 LLM 클라이언트. 공급자는 FRAME_LLM_PROVIDER, 모델은 FRAME_LLM_MODEL."""
    client: LLMClient = registry.build(LLMClient, ctx.settings.llm_provider, ctx)
    return client


def llm_address(ctx: BuildContext) -> str:
    """LLM 요청이 가는 주소: FRAME_LLM_BASE_URL, 없으면 OpenAI. 앞뒤 공백과 끝의 / 를 뗀다."""
    return (ctx.settings.llm_base_url or OPENAI_BASE_URL).strip().rstrip("/")


def vlm_client(registry: AdapterRegistry, ctx: BuildContext) -> LLMClient:
    """이미지를 읽는 LLM 클라이언트. 공급자·키·주소는 구조화와 같고 모델만 FRAME_VLM_MODEL 로 바꾼다."""
    settings = ctx.settings
    model = settings.vlm_model or settings.llm_model
    return llm_client(registry, replace(ctx, settings=replace(settings, llm_model=model)))
