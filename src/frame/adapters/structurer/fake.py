"""테스트용 구조화 어댑터: 미리 정한 IR 을 돌려주거나, 오류를 주입하거나, 규칙 기반에 위임한다."""

from __future__ import annotations

from frame.application.ports.structurer import RequirementStructurer, StructuringRequest
from frame.domain.ir import IntermediateRepresentation

from .rule_based import RuleBasedStructurer


class FakeStructurer(RequirementStructurer):
    def __init__(
        self,
        ir: IntermediateRepresentation | None = None,
        error: Exception | None = None,
        delegate: RequirementStructurer | None = None,
    ) -> None:
        self._ir = ir
        self._error = error
        self._delegate = delegate or RuleBasedStructurer()
        self.calls: list[StructuringRequest] = []

    def structure(self, request: StructuringRequest) -> IntermediateRepresentation:
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        if self._ir is not None:
            return self._ir
        return self._delegate.structure(request)
