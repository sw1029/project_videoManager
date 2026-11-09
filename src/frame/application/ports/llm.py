"""LLM 호출 포트. 구조화(US-06)와 (향후) VLM 기반 검수가 공통으로 쓴다.

구현 (adapters/llm/)
- replay : 미리 준비한 응답을 돌려주는 테스트·CI 용
- openai : OpenAI API. base_url 을 바꾸면 OpenAI 호환 로컬 서버(vLLM 등, 3090 환경)도 같은 코드로 호출한다
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JsonCompletion:
    text: str  # JSON 문자열
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMClient(ABC):
    """계약
    - json_schema 에 맞는 JSON 문자열을 돌려준다. strict 모드가 아니면 호출자가 검증한다.
    - 실패는 LLMError 하위 예외로 보고한다: LLM_TIMEOUT / LLM_RATE_LIMITED / LLM_UNAVAILABLE / LLM_BAD_RESPONSE.
    - API 키 등 비밀을 예외 메시지·로그·반환값에 남기지 않는다.
    """

    @abstractmethod
    def complete_json(
        self,
        system: str,
        user: str,
        json_schema: Mapping[str, Any],
        *,
        schema_name: str = "response",
        timeout_s: float | None = None,
    ) -> JsonCompletion: ...
