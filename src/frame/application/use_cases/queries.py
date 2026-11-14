"""조회 유스케이스 (US-04 대시보드가 쓰는 읽기 전용 API 의 근거)."""

from __future__ import annotations

from collections.abc import Mapping

from frame.domain.enums import CheckType, Verdict, VersionState
from frame.domain.errors import MediaNotAvailable, NotFound, ValidationError
from frame.domain.identifiers import entity_id as make_entity_id
from frame.domain.metrics import summarize
from frame.domain.models import VideoVersion
from frame.domain.services import comparison

from ..dto import (
    ChecksView,
    DashboardVersion,
    EntityTracksView,
    ProjectDashboardView,
    ProjectDetail,
    VersionComparisonView,
    VersionDetail,
    VersionOverview,
)
from ..ports.unit_of_work import UnitOfWork, UnitOfWorkFactory


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

    def get_video_uri(self, version_id: str) -> str:
        """결과 영상의 저장소 URI. 생성 전이거나 실패한 버전은 MediaNotAvailable."""
        with self._uow_factory() as uow:
            version = self._require_version(uow, version_id)
            if not version.video_uri:
                raise MediaNotAvailable(
                    f"버전 {version_id} 의 결과 영상이 아직 없습니다 (상태 {version.state})",
                    version_id=version_id,
                )
            return version.video_uri

    def get_clip_uri(self, version_id: str, scene_key: str) -> str:
        """장면 클립의 저장소 URI. 장면이 없으면 NotFound, 클립이 아직 없으면 MediaNotAvailable."""
        with self._uow_factory() as uow:
            version = self._require_version(uow, version_id)
            if version.ir is None or not version.ir.has_scene(scene_key):
                raise NotFound(f"장면 {scene_key} 을 찾을 수 없습니다", scene_key=scene_key)
            clip = version.clips.get(scene_key)
            if clip is None:
                raise MediaNotAvailable(
                    f"장면 {scene_key} 의 클립이 아직 없습니다 (상태 {version.state})",
                    scene_key=scene_key,
                )
            return clip.uri

    def get_reference_image_uri(self, version_id: str, reference_image_id: str) -> str:
        """버전에 딸린 참조 이미지(사용자 참조·정체성 기준)의 저장소 URI. 그 버전의 것이 아니면 NotFound."""
        with self._uow_factory() as uow:
            self._require_version(uow, version_id)
            for image in uow.reference_images.list_by_version(version_id):
                if image.reference_image_id == reference_image_id:
                    return image.image_uri
        raise NotFound(
            f"참조 이미지 {reference_image_id} 을 찾을 수 없습니다",
            reference_image_id=reference_image_id,
        )

    def compare_versions(
        self, version_id: str, against: str | None = None
    ) -> VersionComparisonView:
        """version_id(대상)를 against(기준, 생략하면 부모)와 장면별로 비교한다 (US-04 AC-43).

        생성 전·처리 중·실패한 버전도 비교할 수 있다 (클립이 없는 장면은 UNAVAILABLE).
        IR 이 없는 버전은 VersionNotComparable(409), 부모가 없는데 against 를 생략하거나 다른 프로젝트의
        버전을 기준으로 주면 ValidationError(422)다.
        """
        with self._uow_factory() as uow:
            after = self._require_version(uow, version_id)
            comparison.ensure_comparable(after)
            against_id = against if against is not None else after.parent_version_id
            if against_id is None:
                raise ValidationError(
                    f"버전 {version_id} 은 부모가 없는 원본입니다. against 로 비교할 버전을 지정하세요",
                    version_id=version_id,
                )
            before = uow.versions.get(against_id)
            if before is None:
                raise NotFound(
                    f"비교 기준 버전 {against_id} 을 찾을 수 없습니다", version_id=against_id
                )
            if before.project_id != after.project_id:
                raise ValidationError(
                    f"비교 기준 버전 {against_id} 은 같은 프로젝트의 버전이어야 합니다",
                    version_id=against_id,
                )
            comparison.ensure_comparable(before)
            before_view, after_view = self._overview(uow, before), self._overview(uow, after)
        return VersionComparisonView(
            before=before_view,
            after=after_view,
            against_is_parent=after.parent_version_id == before.version_id,
            comparison=comparison.compare_versions(before, after),
            checks_delta=comparison.rate_delta(before_view.summary, after_view.summary),
        )

    def get_dashboard(self, project_id: str) -> ProjectDashboardView:
        """버전 트리, 버전별 상태·검수 요약·부모 대비 바뀐 장면 수, 프로젝트 집계 (US-04 AC-41)."""
        with self._uow_factory() as uow:
            project = uow.projects.get(project_id)
            if project is None:
                raise NotFound(f"프로젝트 {project_id} 을 찾을 수 없습니다", project_id=project_id)
            versions = sorted(uow.versions.list_by_project(project_id), key=lambda v: v.created_at)
            overviews = [self._overview(uow, v) for v in versions]

        by_id = {v.version_id: v for v in versions}
        children: dict[str, list[str]] = {v.version_id: [] for v in versions}
        for v in versions:
            if v.parent_version_id in children:
                children[v.parent_version_id].append(v.version_id)
        state_counts = dict.fromkeys(VersionState, 0)
        for v in versions:
            state_counts[v.state] += 1
        completed = [v.version_id for v in versions if v.state is VersionState.COMPLETED]
        return ProjectDashboardView(
            project=project,
            versions=tuple(
                DashboardVersion(
                    overview=overview,
                    depth=_depth(overview.version, by_id),
                    child_version_ids=tuple(children[overview.version.version_id]),
                    scene_count=(
                        len(overview.version.ir.scenes) if overview.version.ir is not None else None
                    ),
                    changed_scene_count=_changed_scene_count(overview.version, by_id),
                )
                for overview in overviews
            ),
            state_counts=state_counts,
            latest_version_id=versions[-1].version_id if versions else None,
            latest_completed_version_id=completed[-1] if completed else None,
        )

    @staticmethod
    def _overview(uow: UnitOfWork, version: VideoVersion) -> VersionOverview:
        results = uow.checks.list_by_version(version.version_id)
        verdicts: dict[str, Verdict] = {}
        for r in results:
            if r.check_type is CheckType.REQUIREMENT and r.requirement_key is not None:
                verdicts.setdefault(r.requirement_key, r.verdict)
        return VersionOverview(
            version=version, summary=summarize(results), requirement_verdicts=verdicts
        )

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


def _depth(version: VideoVersion, by_id: Mapping[str, VideoVersion]) -> int:
    """원본(루트)까지의 거리. 부모가 프로젝트 목록에 없으면 거기서 멈춘다."""
    depth, seen = 0, {version.version_id}
    parent_id = version.parent_version_id
    while parent_id is not None and parent_id in by_id and parent_id not in seen:
        parent = by_id[parent_id]
        seen.add(parent.version_id)
        depth += 1
        parent_id = parent.parent_version_id
    return depth


def _changed_scene_count(version: VideoVersion, by_id: Mapping[str, VideoVersion]) -> int | None:
    parent = by_id.get(version.parent_version_id) if version.parent_version_id else None
    if parent is None or parent.ir is None or version.ir is None:
        return None
    return len(comparison.compare_versions(parent, version).changed_scene_keys)
