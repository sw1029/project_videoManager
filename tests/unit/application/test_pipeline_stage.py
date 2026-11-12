"""PipelineStage / PipelineRunner 의 공통 절차: 단계별 소요 시간 기록 (이슈 #1)."""

from datetime import UTC, datetime

import pytest

from frame.application.errors import TrackingError
from frame.application.pipeline import PipelineContext, PipelineFailure, PipelineRunner
from frame.application.pipeline.stage import PipelineStage
from frame.domain.enums import VersionState
from frame.domain.models import VideoVersion
from tests.support.factories import cafe_ir


class Noop(PipelineStage):
    name = "noop"

    def execute(self, ctx):
        pass


class Boom(PipelineStage):
    name = "boom"

    def execute(self, ctx):
        raise TrackingError("추적기 로드 실패")


def _ctx() -> PipelineContext:
    version = VideoVersion(
        "ver_1", "prj_1", VersionState.PROCESSING, datetime(2026, 11, 12, tzinfo=UTC), ir=cafe_ir()
    )
    return PipelineContext(version=version)


def test_finished_stage_records_its_elapsed_time():
    ctx = _ctx()
    Noop().run(ctx)
    assert ctx.timings_ms["noop"] >= 0


def test_failed_stage_also_records_its_elapsed_time():
    ctx = _ctx()
    with pytest.raises(TrackingError):
        Boom().run(ctx)
    assert ctx.timings_ms["boom"] >= 0


def test_runner_keeps_timings_of_every_stage_reached_before_the_failure():
    ctx = _ctx()
    with pytest.raises(PipelineFailure) as excinfo:
        PipelineRunner().run([Noop(), Boom(), Noop()], ctx)
    assert (excinfo.value.stage, excinfo.value.error_code) == ("boom", "TRACKING_ERROR")
    assert list(ctx.timings_ms) == ["noop", "boom"]
