"""기본 관찰자 (이슈 #3). 컨테이너가 기본으로 연결한다."""

from __future__ import annotations

import logging

from frame.domain.progress import format_elapsed_ms

from .context import PipelineContext
from .stage import PipelineObserver, PipelineStage


class LoggingObserver(PipelineObserver):
    """단계 시작·장면 진행·완료·실패를 로그로 남긴다 (logger: frame.pipeline).

    thread 디스패처에서 생성이 장면당 수 분 걸려도 서버 로그만으로 어디까지 왔는지 알 수 있다.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("frame.pipeline")

    def on_stage_started(self, stage: PipelineStage, ctx: PipelineContext) -> None:
        self._log.info("버전 %s: %s 시작", ctx.version.version_id, stage.name)

    def on_stage_progress(self, stage: PipelineStage, ctx: PipelineContext) -> None:
        progress = ctx.progress.current
        if progress is None:
            return
        item = f" ({progress.current})" if progress.current else ""
        self._log.info(
            "버전 %s: %s 진행 %d/%d%s",
            ctx.version.version_id,
            stage.name,
            progress.done,
            progress.total,
            item,
        )

    def on_stage_finished(
        self, stage: PipelineStage, ctx: PipelineContext, elapsed_ms: float
    ) -> None:
        self._log.info(
            "버전 %s: %s 완료 (%s)",
            ctx.version.version_id,
            stage.name,
            format_elapsed_ms(elapsed_ms),
        )

    def on_stage_failed(
        self, stage: PipelineStage, ctx: PipelineContext, error: BaseException
    ) -> None:
        self._log.warning(
            "버전 %s: %s 실패 (%s, %s): %s",
            ctx.version.version_id,
            stage.name,
            getattr(error, "code", type(error).__name__),
            format_elapsed_ms(ctx.timings_ms.get(stage.name, 0.0)),
            error,
        )
