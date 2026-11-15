"""SQLAlchemy Repository 구현 (Data Mapper): 도메인 객체 <-> 행 변환은 여기서만 일어난다.

IR 은 두 형태로 저장한다.
- video_versions.ir_json : 원본 문서 (도메인 객체 복원용, 정확한 왕복 보장)
- scenes/entities/requirements/scene_entities 행 : ERD 대로 정규화 (조회·무결성 제약·추적 결과 저장용)
조건이 고정(frozen)된 뒤에는 IR 행을 다시 만들지 않고 클립·추적 컬럼만 갱신한다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Update, delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from frame.application.ports.repositories import (
    CheckResultRepository,
    ProjectRepository,
    ReferenceImageRepository,
    TrackRepository,
    VersionRepository,
)
from frame.domain.artifacts import GeneratedClip
from frame.domain.checks import CheckResult, WarningInterval
from frame.domain.enums import CheckType, ReferenceOrigin, TrackingStatus, Verdict, VersionState
from frame.domain.errors import IntegrityViolation
from frame.domain.identifiers import entity_id, requirement_id, scene_id
from frame.domain.ir import IntermediateRepresentation
from frame.domain.models import Project, ReferenceImage, VideoVersion
from frame.domain.progress import PipelineProgress
from frame.domain.revision import RevisionIntent
from frame.domain.state_machine import assert_transition
from frame.domain.tracking import TrackRecord

from .models import (
    CheckResultRow,
    EntityRow,
    ProjectRow,
    ReferenceImageRow,
    RequirementRow,
    SceneEntityRow,
    SceneRow,
    VersionRow,
)


def _flush(session: Session) -> None:
    """flush 시점의 DB 제약 위반(FK/UNIQUE)을 도메인 오류로 바꾼다 (무결성 규칙 2·3·5)."""
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise IntegrityViolation(f"DB 무결성 제약 위반: {exc.orig}") from exc


def _changed_rows(session: Session, statement: Update) -> int:
    """조건부 UPDATE 가 바꾼 행의 수.

    갱신문을 실행한 결과는 행 수를 아는 CursorResult 다. Session.execute 의 타입은 그보다 넓은 Result 라 좁혀서 읽는다.
    """
    return cast("CursorResult[Any]", session.execute(statement)).rowcount


def _utc(value: datetime | None) -> datetime | None:
    """SQLite·MySQL DATETIME 은 시간대를 버리고 돌려준다. 늘 UTC 로 쓰므로 UTC 로 되붙인다 (리스 계산용)."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class SqlAlchemyProjectRepository(ProjectRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, project: Project) -> None:
        self._session.add(
            ProjectRow(
                project_id=project.project_id, title=project.title, created_at=project.created_at
            )
        )
        _flush(self._session)

    def get(self, project_id: str) -> Project | None:
        row = self._session.get(ProjectRow, project_id)
        if row is None:
            return None
        return Project(project_id=row.project_id, title=row.title, created_at=row.created_at)


class SqlAlchemyVersionRepository(VersionRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- 쓰기 -------------------------------------------------------------
    def add(self, version: VideoVersion) -> None:
        self._session.add(self._to_row(version, VersionRow()))
        _flush(self._session)
        if version.ir is not None:
            self._sync_ir_rows(version)

    def save(self, version: VideoVersion) -> None:
        row = self._session.get(VersionRow, version.version_id)
        if row is None:
            raise LookupError(f"저장되지 않은 version_id: {version.version_id}")
        has_ir_rows = (
            self._session.scalar(
                select(SceneRow.scene_id).where(SceneRow.version_id == version.version_id).limit(1)
            )
            is not None
        )
        self._to_row(version, row)
        _flush(self._session)
        if version.ir is not None and (not version.is_frozen or not has_ir_rows):
            self._sync_ir_rows(version)
        self._sync_clip_columns(version)

    def try_transition(
        self, version_id: str, from_state: VersionState, to_state: VersionState
    ) -> bool:
        assert_transition(from_state, to_state)
        changed = _changed_rows(
            self._session,
            update(VersionRow)
            .where(VersionRow.version_id == version_id, VersionRow.state == str(from_state))
            .values(state=str(to_state)),
        )
        self._session.expire_all()
        return changed == 1

    def replace_draft_ir(self, version_id: str, ir: IntermediateRepresentation) -> bool:
        # 조건부 UPDATE 로 행을 잡은 뒤에 정규화 행을 다시 만든다. 동시에 온 생성 접수의 조건부 갱신은
        # 이 트랜잭션이 끝날 때까지 기다리거나(행 잠금) 이미 바뀐 상태를 보고 이쪽이 0 행으로 끝난다
        changed = _changed_rows(
            self._session,
            update(VersionRow)
            .where(
                VersionRow.version_id == version_id,
                VersionRow.state == str(VersionState.DRAFT),
                VersionRow.frozen_fingerprint.is_(None),
            )
            .values(ir_json=ir.to_json()),
        )
        self._session.expire_all()
        if changed != 1:
            return False
        version = self.get(version_id)
        # 방금 바꾼 행을 같은 트랜잭션에서 읽지 못함 = 불변식이 깨짐 (save() 와 같은 예외)
        if version is None:
            raise LookupError(f"조건부 갱신 직후 version_id {version_id} 을 다시 읽지 못했습니다")
        self._sync_ir_rows(version)
        return True

    # ---- 읽기 -------------------------------------------------------------
    def get(self, version_id: str) -> VideoVersion | None:
        row = self._session.get(VersionRow, version_id)
        return None if row is None else self._to_domain(row)

    def list_by_project(self, project_id: str) -> list[VideoVersion]:
        rows = self._session.scalars(
            select(VersionRow).where(VersionRow.project_id == project_id)
        ).all()
        return [self._to_domain(r) for r in rows]

    def list_by_state(self, state: VersionState) -> list[VideoVersion]:
        rows = self._session.scalars(
            select(VersionRow)
            .where(VersionRow.state == str(state))
            .order_by(VersionRow.created_at, VersionRow.version_id)
        ).all()
        return [self._to_domain(r) for r in rows]

    # ---- 매핑 -------------------------------------------------------------
    @staticmethod
    def _to_row(version: VideoVersion, row: VersionRow) -> VersionRow:
        row.version_id = version.version_id
        row.project_id = version.project_id
        row.parent_version_id = version.parent_version_id
        row.state = str(version.state)
        row.queued_at = version.queued_at
        row.started_at = version.started_at
        row.attempt = version.attempt
        row.source_prompt = version.source_prompt
        row.target_duration_ms = version.target_duration_ms
        row.seed = version.seed
        row.ir_json = version.ir.to_json() if version.ir is not None else None
        row.video_uri = version.video_uri
        row.error_code = version.error_code
        row.error_message = version.error_message
        row.frozen_fingerprint = version.frozen_fingerprint
        row.created_at = version.created_at
        row.progress_json = (
            json.dumps(version.progress.to_dict(), ensure_ascii=False)
            if version.progress is not None
            else None
        )
        row.scene_seeds_json = (
            json.dumps(version.scene_seeds, ensure_ascii=False, sort_keys=True)
            if version.scene_seeds
            else None
        )
        row.intent_json = (
            json.dumps(version.intent.to_dict(), ensure_ascii=False)
            if version.intent is not None
            else None
        )
        return row

    def _to_domain(self, row: VersionRow) -> VideoVersion:
        clips: dict[str, GeneratedClip] = {}
        for scene in self._session.scalars(
            select(SceneRow).where(SceneRow.version_id == row.version_id)
        ):
            if scene.clip_uri is not None:
                clips[scene.scene_key] = GeneratedClip(
                    scene_key=scene.scene_key,
                    uri=scene.clip_uri,
                    duration_ms=scene.clip_duration_ms or 0,
                    fingerprint=scene.clip_fingerprint or "",
                    metadata=json.loads(scene.clip_metadata_json)
                    if scene.clip_metadata_json
                    else {},
                )
        return VideoVersion(
            version_id=row.version_id,
            project_id=row.project_id,
            state=VersionState(row.state),
            queued_at=_utc(row.queued_at),
            started_at=_utc(row.started_at),
            attempt=row.attempt or 0,
            created_at=row.created_at,
            parent_version_id=row.parent_version_id,
            source_prompt=row.source_prompt,
            target_duration_ms=row.target_duration_ms,
            seed=row.seed,
            ir=IntermediateRepresentation.from_json(row.ir_json) if row.ir_json else None,
            video_uri=row.video_uri,
            error_code=row.error_code,
            error_message=row.error_message,
            frozen_fingerprint=row.frozen_fingerprint,
            clips=clips,
            progress=(
                PipelineProgress.from_dict(json.loads(row.progress_json))
                if row.progress_json
                else None
            ),
            scene_seeds=(
                {str(k): int(v) for k, v in json.loads(row.scene_seeds_json).items()}
                if row.scene_seeds_json
                else {}
            ),
            intent=(
                RevisionIntent.from_dict(json.loads(row.intent_json)) if row.intent_json else None
            ),
        )

    def _sync_ir_rows(self, version: VideoVersion) -> None:
        ir = version.require_ir()
        vid, pid = version.version_id, version.project_id
        # 기존 행 제거 (DRAFT 단계에서만 호출됨)
        scene_ids = select(SceneRow.scene_id).where(SceneRow.version_id == vid)
        self._session.execute(delete(RequirementRow).where(RequirementRow.scene_id.in_(scene_ids)))
        self._session.execute(delete(SceneEntityRow).where(SceneEntityRow.scene_id.in_(scene_ids)))
        self._session.execute(delete(SceneRow).where(SceneRow.version_id == vid))
        # ENTITY 는 프로젝트 범위: 없으면 만들고 있으면 종류·설명·표시 이름만 갱신
        for e in ir.entities:
            eid = entity_id(pid, e.entity_key)
            existing = self._session.get(EntityRow, eid)
            if existing is None:
                self._session.add(
                    EntityRow(
                        entity_id=eid,
                        project_id=pid,
                        entity_key=e.entity_key,
                        kind=str(e.kind),
                        description=e.description,
                        display_name=e.display_name or None,
                    )
                )
            else:
                existing.kind, existing.description = str(e.kind), e.description
                existing.display_name = e.display_name or None
        _flush(self._session)
        # relationship() 없이 FK 만 선언했으므로 부모 행(SCENE)을 먼저 flush 해야 자식 행 INSERT 순서가 보장된다
        for s in ir.scenes:
            self._session.add(
                SceneRow(
                    scene_id=scene_id(vid, s.scene_key),
                    version_id=vid,
                    scene_key=s.scene_key,
                    planned_start_ms=s.planned_start_ms,
                    planned_end_ms=s.planned_end_ms,
                    description=s.description,
                )
            )
        _flush(self._session)
        for s in ir.scenes:
            sid = scene_id(vid, s.scene_key)
            for ek in s.entity_keys:
                self._session.add(SceneEntityRow(scene_id=sid, entity_id=entity_id(pid, ek)))
            for r in s.requirements:
                self._session.add(
                    RequirementRow(
                        requirement_id=requirement_id(vid, r.key),
                        scene_id=sid,
                        entity_id=entity_id(pid, r.entity_key) if r.entity_key else None,
                        related_entity_id=(
                            entity_id(pid, r.related_entity_key) if r.related_entity_key else None
                        ),
                        predicate=r.predicate,
                        expected_value=r.expected_value,
                    )
                )
        _flush(self._session)

    def _sync_clip_columns(self, version: VideoVersion) -> None:
        if not version.clips:
            return
        for scene in self._session.scalars(
            select(SceneRow).where(SceneRow.version_id == version.version_id)
        ):
            clip = version.clips.get(scene.scene_key)
            if clip is not None:
                scene.clip_uri = clip.uri
                scene.clip_fingerprint = clip.fingerprint
                scene.clip_duration_ms = clip.duration_ms
                scene.clip_metadata_json = (
                    json.dumps(dict(clip.metadata), ensure_ascii=False, default=str)
                    if clip.metadata
                    else None
                )
        _flush(self._session)


class SqlAlchemyReferenceImageRepository(ReferenceImageRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, image: ReferenceImage) -> None:
        self._session.add(
            ReferenceImageRow(
                reference_image_id=image.reference_image_id,
                version_id=image.version_id,
                entity_key=image.entity_key,
                image_uri=image.image_uri,
                origin=str(image.origin),
                source_scene_key=image.source_scene_key,
                source_fingerprint=image.source_fingerprint,
                appearance_fingerprint=image.appearance_fingerprint,
                description=image.description or None,
            )
        )
        _flush(self._session)

    def list_by_version(self, version_id: str) -> list[ReferenceImage]:
        rows = self._session.scalars(
            select(ReferenceImageRow).where(ReferenceImageRow.version_id == version_id)
        ).all()
        return [
            ReferenceImage(
                reference_image_id=r.reference_image_id,
                version_id=r.version_id,
                image_uri=r.image_uri,
                entity_key=r.entity_key,
                origin=ReferenceOrigin(r.origin or ReferenceOrigin.USER),
                source_scene_key=r.source_scene_key,
                source_fingerprint=r.source_fingerprint,
                appearance_fingerprint=r.appearance_fingerprint,
                description=r.description or "",
            )
            for r in rows
        ]


class SqlAlchemyTrackRepository(TrackRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, record: TrackRecord) -> None:
        version = self._session.get(VersionRow, record.version_id)
        if version is None:
            raise IntegrityViolation(f"존재하지 않는 version_id: {record.version_id}")
        key = (
            scene_id(record.version_id, record.scene_key),
            entity_id(version.project_id, record.entity_key),
        )
        row = self._session.get(SceneEntityRow, key)
        if row is None:  # 무결성 규칙 3: 장면에 연결된 객체만 추적 결과를 가질 수 있다
            raise IntegrityViolation(
                f"장면 {record.scene_key} 에 객체 {record.entity_key} 가 연결되어 있지 않습니다"
            )
        row.tracking_status = str(record.status)
        row.track_uri = record.track_uri
        row.track_json = json.dumps(record.to_dict(), ensure_ascii=False)
        _flush(self._session)

    def list_by_version(self, version_id: str) -> list[TrackRecord]:
        stmt = (
            select(SceneEntityRow, SceneRow.scene_key, EntityRow.entity_key)
            .join(SceneRow, SceneRow.scene_id == SceneEntityRow.scene_id)
            .join(EntityRow, EntityRow.entity_id == SceneEntityRow.entity_id)
            .where(SceneRow.version_id == version_id)
            .order_by(
                SceneRow.planned_start_ms, EntityRow.entity_key
            )  # 타임라인 순 (메모리 구현과 동일)
        )
        records = []
        for row, scene_key, entity_key in self._session.execute(stmt):
            if row.track_json:
                records.append(TrackRecord.from_dict(version_id, json.loads(row.track_json)))
            else:
                records.append(
                    TrackRecord(
                        version_id,
                        scene_key,
                        entity_key,
                        status=TrackingStatus(row.tracking_status),
                    )
                )
        return records

    def list_by_entity(self, version_id: str, entity_key: str) -> list[TrackRecord]:
        return [r for r in self.list_by_version(version_id) if r.entity_key == entity_key]


class SqlAlchemyCheckResultRepository(CheckResultRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_all(self, results: Sequence[CheckResult]) -> None:
        for r in results:
            self._session.add(
                CheckResultRow(
                    check_id=r.check_id,
                    version_id=r.version_id,
                    requirement_id=(
                        requirement_id(r.version_id, r.requirement_key)
                        if r.requirement_key
                        else None
                    ),
                    check_type=str(r.check_type),
                    verdict=str(r.verdict),
                    score=r.score,
                    entity_key=r.entity_key,
                    scene_key=r.scene_key,
                    warnings_json=json.dumps([asdict(w) for w in r.warnings], ensure_ascii=False),
                    detail_json=json.dumps(r.detail, ensure_ascii=False, default=str),
                )
            )
        _flush(self._session)

    def list_by_version(self, version_id: str) -> list[CheckResult]:
        rows = self._session.scalars(
            select(CheckResultRow).where(CheckResultRow.version_id == version_id)
        ).all()
        prefix = f"{version_id}:"
        return [
            CheckResult(
                check_id=r.check_id,
                version_id=r.version_id,
                check_type=CheckType(r.check_type),
                verdict=Verdict(r.verdict),
                score=r.score,
                requirement_key=r.requirement_id[len(prefix) :] if r.requirement_id else None,
                entity_key=r.entity_key,
                scene_key=r.scene_key,
                warnings=tuple(WarningInterval(**w) for w in json.loads(r.warnings_json)),
                detail=json.loads(r.detail_json),
            )
            for r in rows
        ]
