"""파이프라인 진행 값 객체 (이슈 #3): 단계 시작 -> 항목(장면) 진행 -> 완료/실패, JSON 왕복."""

import pytest

from frame.domain.progress import (
    PipelineProgress,
    StageProgress,
    StageStatus,
    format_elapsed_ms,
)

RUNNING, COMPLETED, FAILED = StageStatus.RUNNING, StageStatus.COMPLETED, StageStatus.FAILED


def test_stages_are_kept_in_the_order_they_started():
    progress = PipelineProgress().started("generate").finished(1200.4).started("track")

    assert [(s.stage, s.status) for s in progress.stages] == [
        ("generate", COMPLETED),
        ("track", RUNNING),
    ]
    assert progress.current.stage == "track"
    assert progress.stage("generate").elapsed_ms == 1200
    assert progress.stage("check") is None


def test_item_progress_updates_the_running_stage():
    progress = (
        PipelineProgress()
        .started("generate")
        .advanced(done=1, total=3, current="closeup", elapsed_ms=10.2)
    )
    assert progress.current == StageProgress(
        "generate", RUNNING, elapsed_ms=10, done=1, total=3, current="closeup"
    )


def test_finishing_a_stage_marks_every_item_done():
    progress = (
        PipelineProgress()
        .started("generate")
        .advanced(done=2, total=3, current="outro", elapsed_ms=5)
        .finished(7)
    )
    assert progress.current == StageProgress("generate", COMPLETED, elapsed_ms=7, done=3, total=3)


def test_failure_keeps_the_item_that_failed():
    progress = (
        PipelineProgress()
        .started("generate")
        .advanced(done=1, total=3, current="closeup", elapsed_ms=5)
        .failed(9)
    )
    assert progress.current == StageProgress(
        "generate", FAILED, elapsed_ms=9, done=1, total=3, current="closeup"
    )


def test_updates_without_a_running_stage_are_ignored():
    finished = PipelineProgress().started("check").finished(1)
    assert PipelineProgress().advanced(done=1, total=2, current="x", elapsed_ms=1) == (
        PipelineProgress()
    )
    assert finished.advanced(done=1, total=2, current="x", elapsed_ms=1) == finished
    assert finished.failed(5) == finished


def test_total_elapsed_is_the_sum_of_the_stages():
    progress = PipelineProgress().started("generate").finished(1000).started("track").failed(250)
    assert progress.elapsed_ms == 1250


def test_round_trips_through_a_plain_dict():
    progress = (
        PipelineProgress()
        .started("generate")
        .advanced(done=1, total=3, current="closeup", elapsed_ms=3)
        .finished(1500)
        .started("track")
        .advanced(done=0, total=2, current="intro", elapsed_ms=1)
        .failed(3)
    )
    data = progress.to_dict()

    assert data["stages"][0] == {
        "stage": "generate",
        "status": "COMPLETED",
        "elapsed_ms": 1500,
        "done": 3,
        "total": 3,
        "current": None,
    }
    assert PipelineProgress.from_dict(data) == progress


@pytest.mark.parametrize(
    "ms, text", [(0, "0ms"), (12.4, "12ms"), (999.4, "999ms"), (1000, "1.0s"), (95_250, "95.2s")]
)
def test_elapsed_time_is_formatted_for_people(ms, text):
    assert format_elapsed_ms(ms) == text
