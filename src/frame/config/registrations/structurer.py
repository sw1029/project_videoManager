"""요구사항 구조화 (RequirementStructurer) 의 등록 — US-06."""

from __future__ import annotations

from frame.adapters.structurer import FakeStructurer, LLMStructurer, RuleBasedStructurer
from frame.application.ports import RequirementStructurer

from ..registry import AdapterRegistry
from ._dependencies import llm_client


def register(registry: AdapterRegistry) -> None:
    registry.register(RequirementStructurer, "rule_based", lambda ctx: RuleBasedStructurer())
    registry.register(RequirementStructurer, "fake", lambda ctx: FakeStructurer())
    registry.register(
        RequirementStructurer,
        "llm",
        lambda ctx: LLMStructurer(
            llm_client(registry, ctx),
            max_attempts=ctx.settings.llm_max_attempts,
            timeout_s=ctx.settings.llm_timeout_s,
        ),
    )
