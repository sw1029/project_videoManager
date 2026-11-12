"""단계 진행 관찰 (이슈 #3): 장면 단위 진행 통지, LoggingObserver 기본 연결, 관찰자 오류 격리."""

import logging

from frame.adapters.common import FixedClock, SequentialIdGenerator
from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.pipeline import PipelineObserver
from frame.application.pipeline.observers import LoggingObserver
from frame.config import Settings, build_container
from frame.domain.enums import VersionState
from tests.support.factories import create_draft, generate, make_container


class Recorder(PipelineObserver):
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def on_stage_started(self, stage, ctx):
        self.events.append(("started", stage.name))

    def on_stage_progress(self, stage, ctx):
        p = ctx.progress.current
        self.events.append(("progress", stage.name, p.done, p.total, p.current))

    def on_stage_finished(self, stage, ctx, elapsed_ms):
        self.events.append(("finished", stage.name))

    def on_stage_failed(self, stage, ctx, error):
        self.events.append(("failed", stage.name, getattr(error, "code", None)))


def _container_with(*observers, **overrides):
    base = {"ids": SequentialIdGenerator(), "clock": FixedClock(), **overrides}
    return build_container(
        Settings(jobs="recording", persistence="memory"), overrides=base, observers=observers
    )


def test_observers_see_scene_level_progress_of_generation_and_tracking():
    recorder = Recorder()
    container = _container_with(recorder)
    draft = create_draft(container)

    generate(container, draft.version_id)

    assert recorder.events == [
        ("started", "generate"),
        ("progress", "generate", 0, 3, "intro"),
        ("progress", "generate", 1, 3, "closeup"),
        ("progress", "generate", 2, 3, "outro"),
        ("progress", "generate", 3, 3, None),  # 모든 장면 완료 -> 조립
        ("finished", "generate"),
        ("started", "track"),
        ("progress", "track", 0, 3, "intro"),
        ("progress", "track", 1, 3, "closeup"),
        ("progress", "track", 2, 3, "outro"),
        ("finished", "track"),
        ("started", "check"),
        ("finished", "check"),
    ]


def test_observers_see_where_the_pipeline_failed():
    store = InMemoryArtifactStore()
    recorder = Recorder()
    container = _container_with(
        recorder,
        artifact_store=store,
        generator=FakeVideoGenerator(store, fail_scene_keys={"closeup"}),
    )
    draft = create_draft(container)

    generate(container, draft.version_id)

    assert recorder.events[-2:] == [
        ("progress", "generate", 1, 3, "closeup"),
        ("failed", "generate", "GENERATION_ERROR"),
    ]


def test_a_broken_observer_does_not_stop_the_pipeline(caplog):
    class Broken(PipelineObserver):
        def on_stage_started(self, stage, ctx):
            raise RuntimeError("관찰자 고장")

        def on_stage_progress(self, stage, ctx):
            raise RuntimeError("관찰자 고장")

    container = _container_with(Broken())
    draft = create_draft(container)

    with caplog.at_level(logging.ERROR):
        version = generate(container, draft.version_id)

    assert version.state is VersionState.COMPLETED
    assert "관찰자 고장" in caplog.text


def test_logging_observer_reports_start_progress_finish_and_failure(caplog):
    store = InMemoryArtifactStore()
    container = _container_with(
        LoggingObserver(),
        artifact_store=store,
        generator=FakeVideoGenerator(store, fail_scene_keys={"closeup"}),
    )
    draft = create_draft(container)
    vid = draft.version_id

    with caplog.at_level(logging.INFO, logger="frame.pipeline"):
        generate(container, vid)

    messages = [r.getMessage() for r in caplog.records if r.name == "frame.pipeline"]
    assert f"버전 {vid}: generate 시작" in messages
    assert f"버전 {vid}: generate 진행 1/3 (closeup)" in messages
    failures = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(failures) == 1
    assert failures[0].getMessage().startswith(f"버전 {vid}: generate 실패 (GENERATION_ERROR, ")


def test_container_logs_pipeline_stages_by_default(caplog):
    container = make_container()  # observers 를 주지 않으면 LoggingObserver 가 붙는다
    draft = create_draft(container)
    vid = draft.version_id

    with caplog.at_level(logging.INFO, logger="frame.pipeline"):
        generate(container, vid)

    messages = [r.getMessage() for r in caplog.records if r.name == "frame.pipeline"]
    assert f"버전 {vid}: generate 시작" in messages
    assert any(m.startswith(f"버전 {vid}: check 완료 (") for m in messages)
