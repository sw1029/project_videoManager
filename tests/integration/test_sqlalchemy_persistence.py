"""SQLAlchemy 어댑터 통합 테스트 (SQLite): ERD 정규화 행과 제약이 실제로 만들어지는지."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, inspect, select

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
from frame.domain.artifacts import GeneratedClip
from frame.domain.enums import VersionState
from frame.domain.models import Project, ReferenceImage, VideoVersion
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
        row = session.get(RequirementRow, "ver_1:intro:barista:holds:cup")
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


def test_existing_sqlite_database_gains_new_version_columns(tmp_path, ir):
    """#2: queued_at·started_at·attempt 가 없던 예전 개발 DB 도 기동 때 열이 더해져 그대로 쓸 수 있다."""
    engine = create_engine_from_url(f"sqlite+pysqlite:///{(tmp_path / 'old.db').as_posix()}")
    create_schema(engine)
    uow_factory = sqlalchemy_uow_factory(make_session_factory(engine))
    with uow_factory() as uow:
        uow.projects.add(Project("prj_1", "카페", NOW))
        uow.versions.add(VideoVersion("ver_1", "prj_1", VersionState.DRAFT, NOW, ir=ir))
        uow.commit()
    with engine.begin() as conn:  # 이 기능 이전의 스키마로 되돌린다
        for column in ("queued_at", "started_at", "attempt"):
            conn.exec_driver_sql(f"ALTER TABLE video_versions DROP COLUMN {column}")

    create_schema(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("video_versions")}
    assert {"queued_at", "started_at", "attempt"} <= columns
    with uow_factory() as uow:
        version = uow.versions.get("ver_1")
        assert (version.queued_at, version.started_at, version.attempt) == (None, None, 0)
        assert version.ir == ir


def test_existing_sqlite_database_gains_identity_reference_columns(tmp_path, ir):
    """ADR-0015: 정체성 기준 열이 없던 예전 개발 DB 의 참조 이미지는 사용자 참조(USER)로 읽힌다."""
    engine = create_engine_from_url(f"sqlite+pysqlite:///{(tmp_path / 'old.db').as_posix()}")
    create_schema(engine)
    uow_factory = sqlalchemy_uow_factory(make_session_factory(engine))
    with uow_factory() as uow:
        uow.projects.add(Project("prj_1", "카페", NOW))
        uow.versions.add(VideoVersion("ver_1", "prj_1", VersionState.DRAFT, NOW, ir=ir))
        uow.reference_images.add(ReferenceImage("img_1", "ver_1", "mem://refs/cup.png", "cup"))
        uow.commit()
    new_columns = (
        "origin",
        "source_scene_key",
        "source_fingerprint",
        "appearance_fingerprint",
        "description",
    )
    with engine.begin() as conn:  # 이 기능 이전의 스키마로 되돌린다
        for column in new_columns:
            conn.exec_driver_sql(f"ALTER TABLE reference_images DROP COLUMN {column}")

    create_schema(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("reference_images")}
    assert set(new_columns) <= columns
    with uow_factory() as uow:
        (ref,) = uow.reference_images.list_by_version("ver_1")
        assert ref == ReferenceImage("img_1", "ver_1", "mem://refs/cup.png", "cup")


def test_existing_sqlite_database_gains_the_clip_metadata_column(tmp_path, ir):
    """ADR-0016: 클립 메타데이터 열이 없던 예전 개발 DB 의 클립은 빈 메타데이터로 읽힌다."""
    engine = create_engine_from_url(f"sqlite+pysqlite:///{(tmp_path / 'old.db').as_posix()}")
    create_schema(engine)
    uow_factory = sqlalchemy_uow_factory(make_session_factory(engine))
    with uow_factory() as uow:
        uow.projects.add(Project("prj_1", "카페", NOW))
        version = VideoVersion("ver_1", "prj_1", VersionState.DRAFT, NOW, ir=ir)
        version.clips = {"intro": GeneratedClip("intro", "mem://clips/intro", 4000, "fp")}
        uow.versions.add(version)
        uow.versions.save(version)
        uow.commit()
    with engine.begin() as conn:  # 이 기능 이전의 스키마로 되돌린다
        conn.exec_driver_sql("ALTER TABLE scenes DROP COLUMN clip_metadata_json")

    create_schema(engine)

    assert "clip_metadata_json" in {c["name"] for c in inspect(engine).get_columns("scenes")}
    with uow_factory() as uow:
        assert uow.versions.get("ver_1").clips["intro"] == GeneratedClip(
            "intro", "mem://clips/intro", 4000, "fp"
        )
