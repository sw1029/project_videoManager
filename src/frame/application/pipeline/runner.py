"""PipelineRunner: 단계를 순서대로 실행하고, 실패를 error_code/message 로 정규화한다."""

from __future__ import annotations

from collections.abc import Sequence

from frame.domain.errors import DomainError

from ..errors import AdapterError
from .context import PipelineContext
from .stage import PipelineObserver, PipelineStage


class PipelineFailure(Exception):
    """어느 단계에서든 실행 오류가 나면 후속 단계를 중단하고 이것으로 보고한다."""

    def __init__(self, stage: str, error_code: str, message: str) -> None:
        super().__init__(f"[{stage}] {error_code}: {message}")
        self.stage = stage
        self.error_code = error_code
        self.message = message


class PipelineRunner:
    def __init__(self, observers: Sequence[PipelineObserver] = ()) -> None:
        self._observers = tuple(observers)

    def run(
        self,
        stages: Sequence[PipelineStage],
        ctx: PipelineContext,
        observers: Sequence[PipelineObserver] = (),
    ) -> PipelineContext:
        """observers: 이번 실행에만 붙일 관찰자 (예: 이 버전의 진행 상황을 저장하는 관찰자)."""
        everyone = (*self._observers, *observers)
        for stage in stages:
            try:
                stage.run(ctx, everyone)
            except PipelineFailure:
                raise
            except AdapterError as exc:
                raise PipelineFailure(stage.name, exc.code, str(exc)) from exc
            except DomainError as exc:
                raise PipelineFailure(stage.name, exc.code, exc.message) from exc
            except Exception as exc:  # noqa: BLE001 - 알 수 없는 오류도 FAILED 로 기록해야 한다
                raise PipelineFailure(
                    stage.name, "UNEXPECTED_ERROR", f"{type(exc).__name__}: {exc}"
                ) from exc
        return ctx
