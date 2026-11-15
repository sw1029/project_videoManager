"""PipelineStage: Template Method. run() 이 공통 절차(관찰자 통지·시간 측정·진행 기록)를, execute() 가 본문을 맡는다."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from time import perf_counter

from .context import PipelineContext

log = logging.getLogger(__name__)


class PipelineObserver:
    """단계 진행을 관찰한다 (로깅, 진행률 저장, 메트릭). 필요한 메서드만 재정의한다.

    관찰자에서 난 오류는 기록만 하고 파이프라인을 멈추지 않는다 (부수 기능이 유료 생성을 중단시키지 않게).
    """

    def on_stage_started(self, stage: PipelineStage, ctx: PipelineContext) -> None:  # noqa: B027
        pass

    def on_stage_progress(self, stage: PipelineStage, ctx: PipelineContext) -> None:  # noqa: B027
        """항목(장면) 단위 진행. 값은 ctx.progress.current 의 done/total/current 에 있다."""

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
        ctx.progress = ctx.progress.started(self.name)
        _notify(observers, "on_stage_started", lambda o: o.on_stage_started(self, ctx))
        started = perf_counter()

        def elapsed_ms() -> float:
            return (perf_counter() - started) * 1000.0

        def report(done: int, total: int, current: str | None) -> None:
            ctx.progress = ctx.progress.advanced(
                done=done, total=total, current=current, elapsed_ms=elapsed_ms()
            )
            _notify(observers, "on_stage_progress", lambda o: o.on_stage_progress(self, ctx))

        try:
            with ctx.reporting_to(report):
                self.execute(ctx)
        except BaseException as exc:
            error = exc  # except 블록이 끝나면 exc 는 지워지므로 관찰자 호출용으로 묶어 둔다
            failed_ms = elapsed_ms()
            ctx.timings_ms[self.name] = failed_ms  # 실패한 단계도 남긴다
            ctx.progress = ctx.progress.failed(failed_ms)
            _notify(observers, "on_stage_failed", lambda o: o.on_stage_failed(self, ctx, error))
            raise
        finished_ms = elapsed_ms()
        ctx.timings_ms[self.name] = finished_ms
        ctx.progress = ctx.progress.finished(finished_ms)
        _notify(
            observers, "on_stage_finished", lambda o: o.on_stage_finished(self, ctx, finished_ms)
        )

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> None:
        """단계 본문. 항목(장면) 단위로 일하면 ctx.report_progress(끝낸 수, 전체 수, 지금 항목) 로 알린다."""


def _notify(
    observers: Sequence[PipelineObserver],
    event: str,
    call: Callable[[PipelineObserver], None],
) -> None:
    for observer in observers:
        try:
            call(observer)
        except Exception:  # noqa: BLE001 - 관찰자(로그·진행 저장) 오류가 생성·추적·검수를 멈추지 않게
            log.exception("파이프라인 관찰자 %s.%s 실패", type(observer).__name__, event)
