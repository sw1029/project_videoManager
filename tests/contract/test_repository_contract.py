"""Repository/UnitOfWork 계약 테스트: 메모리 구현과 SQLAlchemy(SQLite) 구현이 같은 스위트를 통과한다.

MySQL 로 돌리려면 FRAME_TEST_DB_URL=mysql+pymysql://... 를 주고 아래 params 에 추가하면 된다.
"""

from datetime import UTC, datetime

import pytest

from frame.adapters.persistence.memory import InMemoryStore, in_memory_uow_factory
from frame.adapters.persistence.sqlalchemy import (
    create_engine_from_url,
    create_schema,
    make_session_factory,
    sqlalchemy_uow_factory,
)
from frame.domain.artifacts import GeneratedClip
from frame.domain.checks import CheckResult, WarningInterval
from frame.domain.enums import CheckType, TrackingStatus, Verdict, VersionState
from frame.domain.errors import IntegrityViolation, InvalidStateTransition
from frame.domain.models import Project, ReferenceImage, VideoVersion
from frame.domain.tracking import BoundingBox, TrackRecord, TrackSegment


def _memory():
    return in_memory_uow_factory(InMemoryStore())


def _sqlite():
    engine = create_engine_from_url("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    return sqlalchemy_uow_factory(make_session_factory(engine))


@pytest.fixture(params=[pytest.param(_memory, id="memory"), pytest.param(_sqlite, id="sqlalchemy")])
def uow_factory(request):
    return request.param()


NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def seeded(uow_factory, ir):
    """프로젝트 1개 + IR 이 있는 DRAFT 버전 1개를 저장해 둔다."""
    with uow_factory() as uow:
        uow.projects.add(Project("prj_1", "카페", NOW))
        uow.versions.add(VideoVersion("ver_1", "prj_1", VersionState.DRAFT, NOW, ir=ir, seed=7))
        uow.commit()
    return uow_factory


def test_project_roundtrip(uow_factory):
    with uow_factory() as uow:
        uow.projects.add(Project("prj_1", "카페", NOW))
        uow.commit()
    with uow_factory() as uow:
        project = uow.projects.get("prj_1")
        assert (project.project_id, project.title) == ("prj_1", "카페")
        assert uow.projects.get("prj_missing") is None


def test_version_roundtrip_with_ir(seeded, ir):
    with seeded() as uow:
        version = uow.versions.get("ver_1")
        assert version.state is VersionState.DRAFT
        assert version.ir == ir
        assert version.seed == 7
        assert [v.version_id for v in uow.versions.list_by_project("prj_1")] == ["ver_1"]


def test_save_persists_state_result_and_clips(seeded):
    with seeded() as uow:
        version = uow.versions.get("ver_1")
        version.transition_to(VersionState.QUEUED)
        version.freeze_conditions()
        version.transition_to(VersionState.PROCESSING)
        version.transition_to(VersionState.COMPLETED)
        version.video_uri = "mem://videos/ver_1.json"
        version.clips = {"intro": GeneratedClip("intro", "mem://clips/intro", 4000, "fp-intro")}
        uow.versions.save(version)
        uow.commit()
    with seeded() as uow:
        loaded = uow.versions.get("ver_1")
        assert loaded.state is VersionState.COMPLETED
        assert loaded.video_uri == "mem://videos/ver_1.json"
        assert loaded.frozen_fingerprint == loaded.ir.fingerprint()
        assert loaded.clips["intro"] == GeneratedClip(
            "intro", "mem://clips/intro", 4000, "fp-intro"
        )


def test_save_persists_failure_info(seeded):
    with seeded() as uow:
        version = uow.versions.get("ver_1")
        version.transition_to(VersionState.QUEUED)
        version.mark_failed("GENERATION_ERROR", "GPU 메모리 부족")
        uow.versions.save(version)
        uow.commit()
    with seeded() as uow:
        loaded = uow.versions.get("ver_1")
        assert (loaded.state, loaded.error_code, loaded.error_message) == (
            VersionState.FAILED,
            "GENERATION_ERROR",
            "GPU 메모리 부족",
        )


def test_try_transition_is_conditional(seeded):
    with seeded() as uow:
        assert uow.versions.try_transition("ver_1", VersionState.DRAFT, VersionState.QUEUED) is True
        assert (
            uow.versions.try_transition("ver_1", VersionState.DRAFT, VersionState.QUEUED) is False
        )
        assert uow.versions.get("ver_1").state is VersionState.QUEUED
        assert (
            uow.versions.try_transition("ver_missing", VersionState.DRAFT, VersionState.QUEUED)
            is False
        )
        uow.commit()
    with seeded() as uow:
        assert uow.versions.get("ver_1").state is VersionState.QUEUED


def test_try_transition_rejects_illegal_pairs(seeded):
    with seeded() as uow, pytest.raises(InvalidStateTransition):
        uow.versions.try_transition("ver_1", VersionState.DRAFT, VersionState.COMPLETED)


def test_unsaved_changes_are_not_visible(seeded):
    with seeded() as uow:
        version = uow.versions.get("ver_1")
        version.video_uri = "mem://leak"
        # save() 호출 없음
        uow.commit()
    with seeded() as uow:
        assert uow.versions.get("ver_1").video_uri is None


def test_reference_images_roundtrip(seeded):
    with seeded() as uow:
        uow.reference_images.add(ReferenceImage("img_1", "ver_1", "mem://refs/cup.png", "cup"))
        uow.reference_images.add(ReferenceImage("img_2", "ver_1", "mem://refs/style.png"))
        uow.commit()
    with seeded() as uow:
        refs = uow.reference_images.list_by_version("ver_1")
        assert [(r.reference_image_id, r.entity_key) for r in refs] == [
            ("img_1", "cup"),
            ("img_2", None),
        ]
        assert uow.reference_images.list_by_version("ver_missing") == []


def test_tracks_upsert_and_query(seeded):
    record = TrackRecord(
        "ver_1",
        "intro",
        "cup",
        TrackingStatus.COMPLETED,
        (TrackSegment(0, 4000, (BoundingBox(0, 0.1, 0.2, 0.2, 0.3),)),),
        "mem://tracks/cup",
    )
    with seeded() as uow:
        uow.tracks.upsert(record)
        uow.commit()
    with seeded() as uow:
        cup = uow.tracks.list_by_entity("ver_1", "cup")
        assert cup[0] == record
        # 추적 전인 (장면, 객체) 연결도 PENDING 행으로 보이며, 타임라인 순으로 정렬된다
        assert [(t.scene_key, t.status) for t in cup] == [
            ("intro", TrackingStatus.COMPLETED),
            ("closeup", TrackingStatus.PENDING),
        ]
        all_tracks = uow.tracks.list_by_version("ver_1")
        assert len(all_tracks) == 7  # intro 3 + closeup 2 + outro 2 (SCENE_ENTITY 행 수)
        assert [t.scene_key for t in all_tracks] == ["intro"] * 3 + ["closeup"] * 2 + ["outro"] * 2
    updated = TrackRecord("ver_1", "intro", "cup", TrackingStatus.FAILED)
    with seeded() as uow:
        uow.tracks.upsert(updated)
        uow.commit()
    with seeded() as uow:
        assert uow.tracks.list_by_entity("ver_1", "cup")[0].status is TrackingStatus.FAILED


def test_track_requires_scene_entity_link(seeded):
    """무결성 규칙 3: 장면에 연결되지 않은 객체의 추적 결과는 저장할 수 없다 (cup 은 outro 에 없다)."""
    with pytest.raises(IntegrityViolation), seeded() as uow:
        uow.tracks.upsert(TrackRecord("ver_1", "outro", "cup", TrackingStatus.COMPLETED))


def test_check_results_roundtrip(seeded):
    results = [
        CheckResult(
            "chk_1",
            "ver_1",
            CheckType.REQUIREMENT,
            Verdict.PASS,
            1.0,
            requirement_key="intro:cup:color",
            entity_key="cup",
            scene_key="intro",
            detail={"predicate": "color"},
        ),
        CheckResult(
            "chk_2",
            "ver_1",
            CheckType.CONSISTENCY,
            Verdict.FAIL,
            0.6,
            entity_key="cup",
            warnings=(WarningInterval("closeup", 5000, 6000, "컵 색상 불일치"),),
        ),
    ]
    with seeded() as uow:
        uow.checks.add_all(results)
        uow.commit()
    with seeded() as uow:
        loaded = sorted(uow.checks.list_by_version("ver_1"), key=lambda r: r.check_id)
        assert loaded == results
        assert uow.checks.list_by_version("ver_missing") == []


def test_check_result_must_reference_requirement_of_same_version(seeded):
    bad = CheckResult(
        "chk_x", "ver_1", CheckType.REQUIREMENT, Verdict.PASS, requirement_key="outro:cup:color"
    )
    with pytest.raises(IntegrityViolation), seeded() as uow:
        uow.checks.add_all([bad])
        uow.commit()
