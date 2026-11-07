"""SQLAlchemy 어댑터 통합 테스트 (SQLite): ERD 정규화 행과 제약이 실제로 만들어지는지."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from frame.adapters.persistence.sqlalchemy import (
    create_engine_from_url,
    create_schema,
    make_session_factory,
    sqlalchemy_uow_factory,
)
from frame.adapters.persistence.sqlalchemy.models import (
    EntityRow,
    RequirementRow,
    SceneEntityRow,
    SceneRow,
)
from frame.config import Settings
from frame.domain.enums import VersionState
from frame.domain.models import Project, VideoVersion
from tests.support.factories import create_draft, generate, make_container

NOW = datetime(2026, 11, 7, tzinfo=UTC)


@pytest.fixture
def engine():
    engine = create_engine_from_url("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    return engine


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_ir_is_normalized_into_erd_rows(engine, ir):
    uow_factory = sqlalchemy_uow_factory(make_session_factory(engine))
    with uow_factory() as uow:
        uow.projects.add(Project("prj_1", "카페", NOW))
        uow.versions.add(VideoVersion("ver_1", "prj_1", VersionState.DRAFT, NOW, ir=ir))
        uow.commit()

    with make_session_factory(engine)() as session:
        assert _count(session, SceneRow) == 3
        assert _count(session, EntityRow) == 3
        assert _count(session, SceneEntityRow) == 7  # intro 3 + closeup 2 + outro 2
        assert _count(session, RequirementRow) == 8
        row = session.get(RequirementRow, "ver_1:intro:barista:holds")
        assert (row.entity_id, row.related_entity_id) == ("prj_1:barista", "prj_1:cup")


def test_entities_are_shared_across_versions_of_a_project(engine, ir):
    uow_factory = sqlalchemy_uow_factory(make_session_factory(engine))
    with uow_factory() as uow:
        uow.projects.add(Project("prj_1", "카페", NOW))
        uow.versions.add(VideoVersion("ver_1", "prj_1", VersionState.DRAFT, NOW, ir=ir))
        uow.versions.add(
            VideoVersion(
                "ver_2", "prj_1", VersionState.DRAFT, NOW, ir=ir, parent_version_id="ver_1"
            )
        )
        uow.commit()
    with make_session_factory(engine)() as session:
        assert _count(session, EntityRow) == 3  # entity_key 는 프로젝트 내 유일 (규칙 2)
        assert _count(session, SceneRow) == 6


def test_full_flow_runs_on_sqlalchemy_persistence():
    """메모리 저장소로 통과한 유스케이스 흐름이 SQL 저장소에서도 그대로 통과한다 (교체 가능성 검증)."""
    container = make_container(Settings(jobs="recording", persistence="sqlalchemy"))
    draft = create_draft(container)
    assert container.queries.get_version(draft.version_id).version.state is VersionState.DRAFT

    version = generate(container, draft.version_id)
    assert version.state is VersionState.COMPLETED
    assert set(version.clips) == {"intro", "closeup", "outro"}
    checks = container.queries.get_checks(draft.version_id)
    assert checks.summary.requirement_reflection_rate == 1.0
    tracks = container.queries.get_entity_tracks(draft.version_id, "cup").records
    assert [t.scene_key for t in tracks] == ["intro", "closeup"]
