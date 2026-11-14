"""LLM 클라이언트 (LLMClient) 의 등록. 구조화·변경 요청 해석·검수·정체성 기준 추출이 함께 쓴다."""

from __future__ import annotations

from frame.adapters.llm import ReplayLLMClient
from frame.adapters.llm.key_store import LLMKeyStore
from frame.adapters.llm.openai_client import OpenAIJsonClient
from frame.application.errors import ConfigurationError
from frame.application.ports.llm import LLMClient

from ..registry import AdapterRegistry, BuildContext


def register(registry: AdapterRegistry) -> None:
    registry.register(LLMClient, "fake", lambda ctx: ReplayLLMClient())
    registry.register(LLMClient, "openai", _openai)


def _openai(ctx: BuildContext) -> LLMClient:
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
    except ImportError as exc:  # openai 패키지(llm extra)는 클라이언트를 만들 때 import 된다
        raise ConfigurationError(
            "FRAME_STRUCTURER=llm 에 필요한 openai 패키지가 없습니다. "
            'pip install -e ".[llm]" 로 설치하세요'
        ) from exc
