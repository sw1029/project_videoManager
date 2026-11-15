"""진행 상황·부분 결과의 SQL 저장 (이슈 #3, SQLite): 메모리 저장소와 같은 결과가 DB 왕복 뒤에도 나온다."""

from datetime import UTC, datetime

from sqlalchemy import select

from frame.adapters.generator import FakeVideoGenerator
from frame.adapters.persistence.sqlalchemy import (
    create_engine_from_url,
    create_schema,
    make_session_factory,
    sqlalchemy_uow_factory,
)
from frame.adapters.persistence.sqlalchemy.models import VersionRow
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.ports.generator import VideoGenerator
from frame.config import Settings
from frame.domain.enums import VersionState
from frame.domain.models import Project, VideoVersion
from frame.domain.progress import PipelineProgress, StageStatus
from tests.support.factories import cafe_ir, create_draft, generate, make_container

SQL = Settings(jobs="recording", persistence="sqlalchemy")


def _stages(version):
    return [(s.stage, s.status, s.done, s.total, s.current) for s in version.progress.stages]


def test_progress_round_trips_as_json():
    engine = create_engine_from_url("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    uow_factory = sqlalchemy_uow_factory(make_session_factory(engine))
    progress = (
        PipelineProgress()
        .started("generate")
        .advanced(done=1, total=3, current="closeup", elapsed_ms=12)
    )
    now = datetime(2026, 11, 12, tzinfo=UTC)
    with uow_factory() as uow:
        uow.projects.add(Project("prj_1", "카페", now))
        uow.versions.add(VideoVersion("ver_1", "prj_1", VersionState.DRAFT, now, ir=cafe_ir()))
        uow.commit()
    with uow_factory() as uow:
        version = uow.versions.get("ver_1")
        assert version.progress is None
        version.progress = progress
        uow.versions.save(version)
        uow.commit()

    with uow_factory() as uow:
        assert uow.versions.get("ver_1").progress == progress
    with make_session_factory(engine)() as session:
        raw = session.scalar(select(VersionRow.progress_json))
    assert '"current": "closeup"' in raw


def test_failed_version_keeps_partial_clips_and_progress_in_the_database():
    store = InMemoryArtifactStore()
    container = make_container(
        SQL, artifact_store=store, generator=FakeVideoGenerator(store, fail_scene_keys={"closeup"})
    )
    vid = create_draft(container).version_id

    version = generate(container, vid)

    assert (version.state, version.error_code) == (VersionState.FAILED, "GENERATION_ERROR")
    assert set(version.clips) == {"intro"}
    assert container.queries.get_clip_uri(vid, "intro") == version.clips["intro"].uri
    assert _stages(version) == [("generate", StageStatus.FAILED, 1, 3, "closeup")]
    assert "진행: 장면 closeup (2/3) 에서 실패" in version.error_message


def test_completed_version_progress_is_stored():
    container = make_container(SQL)
    vid = create_draft(container).version_id

    version = generate(container, vid)

    assert [s.status for s in version.progress.stages] == [StageStatus.COMPLETED] * 3
    assert _stages(version)[0] == ("generate", StageStatus.COMPLETED, 3, 3, None)


def test_progress_is_visible_while_the_pipeline_runs():
    class Probe(VideoGenerator):
        def __init__(self, inner):
            self.inner, self.queries, self.seen = inner, None, {}

        def generate_scene(self, request):
            stored = self.queries.get_version(request.version_id).version
            self.seen[request.scene.scene_key] = (stored.progress.current, set(stored.clips))
            return self.inner.generate_scene(request)

    store = InMemoryArtifactStore()
    probe = Probe(FakeVideoGenerator(store))
    container = make_container(SQL, artifact_store=store, generator=probe)
    probe.queries = container.queries
    vid = create_draft(container).version_id

    generate(container, vid)

    current, clips = probe.seen["closeup"]
    assert (current.stage, current.status, current.done, current.current) == (
        "generate",
        StageStatus.RUNNING,
        1,
        "closeup",
    )
    assert clips == {"intro"}
