"""Repository/UnitOfWork 계약 테스트: 메모리 구현과 SQLAlchemy(SQLite) 구현이 같은 스위트를 통과한다.

MySQL 로 돌리려면 FRAME_TEST_DB_URL=mysql+pymysql://... 를 주고 아래 params 에 추가하면 된다.
"""

from datetime import UTC, datetime, timedelta

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
from frame.domain.enums import (
    CheckType,
    ReferenceOrigin,
    TrackingStatus,
    Verdict,
    VersionState,
)
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


def test_clip_metadata_survives_a_round_trip(seeded):
    """ADR-0016: 부모 클립의 생성 기록(모델·작업 id)을 수정 버전의 생성기에 넘기려면 저장돼 있어야 한다."""
    metadata = {
        "generator": "openrouter",
        "model": "alibaba/wan-3.0",
        "job_id": "gen-vid-1789480874-Ab3dEf9hIjKlMnOpQrSt",
        "cost_usd": 0.2125,
        "seed": 7,
        "output": {"width": 480, "height": 854, "fps": 30.0, "has_audio": False},
        "prompt": "Scene: 바리스타가 컵을 들어 보인다",
        "cache_hit": True,
        "nothing": None,
    }
    with seeded() as uow:
        version = uow.versions.get("ver_1")
        version.clips = {
            "intro": GeneratedClip("intro", "mem://clips/intro", 4000, "fp-intro", metadata),
            "closeup": GeneratedClip("closeup", "mem://clips/closeup", 4000, "fp-closeup"),
        }
        uow.versions.save(version)
        uow.commit()
    with seeded() as uow:
        clips = uow.versions.get("ver_1").clips
        assert clips["intro"].metadata == metadata
        assert clips["closeup"].metadata == {}


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
        assert all(r.origin is ReferenceOrigin.USER and not r.is_identity for r in refs)


def test_identity_reference_roundtrip(seeded):
    """ADR-0015: 클립에서 뽑은 정체성 기준 이미지는 출처·기준 장면·해시·설명까지 그대로 돌아온다."""
    anchor = ReferenceImage(
        "img_9",
        "ver_1",
        "mem://references/identity/abc/barista.png",
        "barista",
        origin=ReferenceOrigin.DERIVED,
        source_scene_key="intro",
        source_fingerprint="f" * 64,
        appearance_fingerprint="a" * 64,
        description="흰 셔츠, 짧은 검은 머리",
    )
    with seeded() as uow:
        uow.reference_images.add(anchor)
        uow.commit()
    with seeded() as uow:
        (stored,) = uow.reference_images.list_by_version("ver_1")
        assert stored == anchor
        assert stored.is_identity


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


def test_execution_record_roundtrip(seeded):
    """#2: 접수·시작 시각과 시도 횟수. SQLite 도 시간대(UTC)가 있는 값으로 돌려준다."""
    queued_at = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    started_at = queued_at + timedelta(seconds=5)
    with seeded() as uow:
        version = uow.versions.get("ver_1")
        version.transition_to(VersionState.QUEUED)
        version.record_queued(queued_at)
        version.transition_to(VersionState.PROCESSING)
        version.record_started(started_at)
        uow.versions.save(version)
        uow.commit()
    with seeded() as uow:
        loaded = uow.versions.get("ver_1")
        assert (loaded.queued_at, loaded.started_at, loaded.attempt) == (queued_at, started_at, 1)
        assert loaded.started_at.utcoffset() == timedelta(0)


def test_list_by_state_returns_versions_in_creation_order(seeded, ir):
    with seeded() as uow:
        for n, state in enumerate(
            [VersionState.QUEUED, VersionState.PROCESSING, VersionState.QUEUED], start=2
        ):
            uow.versions.add(
                VideoVersion(f"ver_{n}", "prj_1", state, NOW + timedelta(seconds=n), ir=ir)
            )
        uow.commit()
    with seeded() as uow:
        assert [v.version_id for v in uow.versions.list_by_state(VersionState.QUEUED)] == [
            "ver_2",
            "ver_4",
        ]
        processing = uow.versions.list_by_state(VersionState.PROCESSING)
        assert [v.version_id for v in processing] == ["ver_3"]
        assert processing[0].ir == ir
        assert uow.versions.list_by_state(VersionState.COMPLETED) == []


def test_revision_intent_and_scene_seeds_roundtrip(seeded, ir):
    """수정 버전의 의도(종류·유지할 요소·요청 원문)와 장면별 seed 재지정이 저장소를 거쳐도 그대로다 (ADR-0017)."""
    from frame.domain.enums import ChangeScope, KeepAspect, RevisionKind
    from frame.domain.revision import KeepItem, RevisionIntent

    intent = RevisionIntent(
        RevisionKind.REPAIR,
        "color",
        "red",
        entity_key="cup",
        scope=ChangeScope.SCENES,
        target_scene_keys=("closeup",),
        keep=(KeepItem(KeepAspect.ACTION, "barista"), KeepItem(KeepAspect.LIGHTING)),
        request_text="컵 색상만 다시 맞춰줘",
    )
    with seeded() as uow:
        uow.versions.add(
            VideoVersion(
                "ver_2",
                "prj_1",
                VersionState.DRAFT,
                NOW,
                parent_version_id="ver_1",
                ir=ir,
                seed=7,
                scene_seeds={"closeup": 8},
                intent=intent,
            )
        )
        uow.commit()

    with seeded() as uow:
        loaded, original = uow.versions.get("ver_2"), uow.versions.get("ver_1")
    assert loaded.intent == intent and loaded.scene_seeds == {"closeup": 8}
    assert (loaded.seed_for("closeup"), loaded.seed_for("intro")) == (8, 7)
    assert original.intent is None and original.scene_seeds == {}


def _outro_without_barista(ir):
    """outro 에서 바리스타를 뺀 IR (DRAFT IR 수정의 예)."""
    from dataclasses import replace

    outro = ir.scene("outro")
    return ir.with_scene(
        replace(
            outro,
            entity_keys=("cafe",),
            requirements=tuple(r for r in outro.requirements if r.entity_key == "cafe"),
        )
    )


def test_replace_draft_ir_swaps_the_ir_and_its_rows(seeded, ir):
    """#11: DRAFT 의 IR 을 바꾸면 정규화 행(장면-객체 연결·요구조건)도 새 IR 을 따른다."""
    edited = _outro_without_barista(ir)
    with seeded() as uow:
        assert uow.versions.replace_draft_ir("ver_1", edited) is True
        uow.commit()
    with seeded() as uow:
        assert uow.versions.get("ver_1").ir == edited
        assert uow.versions.get("ver_1").state is VersionState.DRAFT
        links = [(t.scene_key, t.entity_key) for t in uow.tracks.list_by_version("ver_1")]
        assert ("outro", "barista") not in links and ("outro", "cafe") in links
    removed = CheckResult(
        "chk_x",
        "ver_1",
        CheckType.REQUIREMENT,
        Verdict.PASS,
        requirement_key="outro:barista:shirt_color",
    )
    with pytest.raises(IntegrityViolation), seeded() as uow:
        uow.checks.add_all([removed])  # 지운 요구조건은 더 이상 이 버전에 없다
        uow.commit()


def test_replace_draft_ir_leaves_queued_frozen_or_missing_versions_alone(seeded, ir):
    """#11: 생성이 접수된(QUEUED) 버전, 조건이 고정된 버전, 없는 버전의 IR 은 바꾸지 않는다 (조건부 갱신)."""
    edited = _outro_without_barista(ir)
    with seeded() as uow:
        uow.versions.add(VideoVersion("ver_2", "prj_1", VersionState.DRAFT, NOW, ir=ir))
        frozen = uow.versions.get("ver_2")
        frozen.freeze_conditions()
        uow.versions.save(frozen)
        assert uow.versions.try_transition("ver_1", VersionState.DRAFT, VersionState.QUEUED)
        assert uow.versions.replace_draft_ir("ver_1", edited) is False
        assert uow.versions.replace_draft_ir("ver_2", edited) is False
        assert uow.versions.replace_draft_ir("ver_missing", edited) is False
        uow.commit()
    with seeded() as uow:
        assert uow.versions.get("ver_1").ir == ir
        assert uow.versions.get("ver_2").ir == ir
