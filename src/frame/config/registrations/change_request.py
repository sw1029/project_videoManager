"""글로 쓴 변경 요청의 해석 (ChangeRequestInterpreter) 의 등록 — US-01, ADR-0019."""

from __future__ import annotations

from frame.adapters.change_request import (
    LLMChangeRequestInterpreter,
    RuleBasedChangeRequestInterpreter,
)
from frame.application.ports.change_request import ChangeRequestInterpreter

from ..registry import AdapterRegistry
from ._dependencies import llm_client


def register(registry: AdapterRegistry) -> None:
    registry.register(
        ChangeRequestInterpreter, "rule_based", lambda ctx: RuleBasedChangeRequestInterpreter()
    )
    registry.register(
        ChangeRequestInterpreter,
        "llm",
        lambda ctx: LLMChangeRequestInterpreter(
            llm_client(registry, ctx), timeout_s=ctx.settings.llm_timeout_s
        ),
    )
