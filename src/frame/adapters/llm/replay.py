"""미리 준비한 응답을 순서대로 돌려주는 LLM 클라이언트 (테스트·CI·오프라인 데모용)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from frame.application.errors import LLMBadResponse
from frame.application.ports.llm import JsonCompletion, LLMClient


class ReplayLLMClient(LLMClient):
    def __init__(self, responses: Sequence[str | Exception] = (), model: str = "replay") -> None:
        self._responses: list[str | Exception] = list(responses)
        self.model = model
        self.calls: list[dict[str, Any]] = []  # 스파이: 프롬프트·스키마 단언용

    def complete_json(
        self,
        system: str,
        user: str,
        json_schema: Mapping[str, Any],
        *,
        schema_name: str = "response",
        timeout_s: float | None = None,
    ) -> JsonCompletion:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "schema_name": schema_name,
                "schema": dict(json_schema),
                "timeout_s": timeout_s,
            }
        )
        if not self._responses:
            raise LLMBadResponse("재생할 응답이 더 없습니다 (ReplayLLMClient)")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return JsonCompletion(text=item, model=self.model)
