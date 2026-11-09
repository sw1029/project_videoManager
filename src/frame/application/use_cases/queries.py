"""조회 유스케이스 (US-04 대시보드가 쓰는 읽기 전용 API 의 근거)."""

from __future__ import annotations

from frame.domain.errors import NotFound
from frame.domain.identifiers import entity_id as make_entity_id
from frame.domain.metrics import summarize
from frame.domain.models import VideoVersion

from ..dto import ChecksView, EntityTracksView, ProjectDetail, VersionDetail
from ..ports.unit_of_work import UnitOfWorkFactory


class ProjectQueries:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get_project(self, project_id: str) -> ProjectDetail:
        with self._uow_factory() as uow:
            project = uow.projects.get(project_id)
            if project is None:
                raise NotFound(f"프로젝트 {project_id} 을 찾을 수 없습니다", project_id=project_id)
            versions = sorted(uow.versions.list_by_project(project_id), key=lambda v: v.created_at)
            return ProjectDetail(project=project, versions=tuple(versions))

    def get_version(self, version_id: str) -> VersionDetail:
        with self._uow_factory() as uow:
            version = self._require_version(uow, version_id)
            references = tuple(uow.reference_images.list_by_version(version_id))
            return VersionDetail(version=version, reference_images=references)

    def get_checks(self, version_id: str) -> ChecksView:
        with self._uow_factory() as uow:
            version = self._require_version(uow, version_id)
            results = tuple(uow.checks.list_by_version(version_id))
            return ChecksView(
                version_id=version_id,
                state=version.state,
                results=results,
                summary=summarize(results),
            )

    def get_entity_tracks(self, version_id: str, entity_ref: str) -> EntityTracksView:
        """entity_ref 는 entity_key 또는 파생 entity_id(project_id:entity_key) 모두 허용한다."""
        with self._uow_factory() as uow:
            version = self._require_version(uow, version_id)
            entity_key = self._resolve_entity_key(version, entity_ref)
            records = tuple(uow.tracks.list_by_entity(version_id, entity_key))
            return EntityTracksView(version_id=version_id, entity_key=entity_key, records=records)

    @staticmethod
    def _require_version(uow, version_id: str) -> VideoVersion:  # type: ignore[no-untyped-def]
        version = uow.versions.get(version_id)
        if version is None:
            raise NotFound(f"버전 {version_id} 을 찾을 수 없습니다", version_id=version_id)
        return version

    @staticmethod
    def _resolve_entity_key(version: VideoVersion, entity_ref: str) -> str:
        if version.ir is None:
            raise NotFound("중간표현이 없는 버전입니다", version_id=version.version_id)
        for entity in version.ir.entities:
            if entity_ref in (
                entity.entity_key,
                make_entity_id(version.project_id, entity.entity_key),
            ):
                return entity.entity_key
        raise NotFound(f"객체 {entity_ref} 을 찾을 수 없습니다", entity_ref=entity_ref)
