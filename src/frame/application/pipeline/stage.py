"""PipelineStage: Template Method. run() 이 공통 절차(관찰자 통지·시간 측정)를, execute() 가 본문을 맡는다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from time import perf_counter

from .context import PipelineContext


class PipelineObserver:
    """단계 진행을 관찰한다 (로깅, 진행률 저장, 메트릭). 필요한 메서드만 재정의한다."""

    def on_stage_started(self, stage: PipelineStage, ctx: PipelineContext) -> None:  # noqa: B027
        pass

    def on_stage_finished(  # noqa: B027
        self, stage: PipelineStage, ctx: PipelineContext, elapsed_ms: float
    ) -> None:
        pass

    def on_stage_failed(  # noqa: B027
        self, stage: PipelineStage, ctx: PipelineContext, error: BaseException
    ) -> None:
        pass


class PipelineStage(ABC):
    name: str = "stage"

    def run(self, ctx: PipelineContext, observers: Sequence[PipelineObserver] = ()) -> None:
        for observer in observers:
            observer.on_stage_started(self, ctx)
        started = perf_counter()
        try:
            self.execute(ctx)
        except BaseException as exc:
            ctx.timings_ms[self.name] = (perf_counter() - started) * 1000.0  # 실패한 단계도 남긴다
            for observer in observers:
                observer.on_stage_failed(self, ctx, exc)
            raise
        elapsed_ms = (perf_counter() - started) * 1000.0
        ctx.timings_ms[self.name] = elapsed_ms
        for observer in observers:
            observer.on_stage_finished(self, ctx, elapsed_ms)

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> None: ...
