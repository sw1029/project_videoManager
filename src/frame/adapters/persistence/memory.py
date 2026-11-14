"""메모리 저장소. 실제 DB 처럼 동작하도록 add/get/save 에서 깊은 복사를 한다.

(저장하지 않은 변경이 조용히 반영되는 일이 없으므로, 'save 를 빠뜨린' 버그를 테스트가 잡아낸다.)
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field

from frame.application.ports.repositories import (
    CheckResultRepository,
    ProjectRepository,
    ReferenceImageRepository,
    TrackRepository,
    VersionRepository,
)
from frame.application.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from frame.domain.checks import CheckResult
from frame.domain.enums import VersionState
from frame.domain.errors import IntegrityViolation
from frame.domain.ir import IntermediateRepresentation
from frame.domain.models import Project, ReferenceImage, VideoVersion
from frame.domain.state_machine import assert_transition
from frame.domain.tracking import TrackRecord


@dataclass
class InMemoryStore:
    projects: dict[str, Project] = field(default_factory=dict)
    versions: dict[str, VideoVersion] = field(default_factory=dict)
    reference_images: dict[str, list[ReferenceImage]] = field(default_factory=dict)
    tracks: dict[tuple[str, str, str], TrackRecord] = field(default_factory=dict)
    checks: dict[str, list[CheckResult]] = field(default_factory=dict)


class InMemoryProjectRepository(ProjectRepository):
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def add(self, project: Project) -> None:
        if project.project_id in self._store.projects:
            raise IntegrityViolation(f"중복 project_id: {project.project_id}")
        self._store.projects[project.project_id] = copy.deepcopy(project)

    def get(self, project_id: str) -> Project | None:
        return copy.deepcopy(self._store.projects.get(project_id))


class InMemoryVersionRepository(VersionRepository):
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def add(self, version: VideoVersion) -> None:
        if version.version_id in self._store.versions:
            raise IntegrityViolation(f"중복 version_id: {version.version_id}")
        if version.project_id not in self._store.projects:
            raise IntegrityViolation(f"존재하지 않는 project_id: {version.project_id}")
        self._store.versions[version.version_id] = copy.deepcopy(version)

    def get(self, version_id: str) -> VideoVersion | None:
        return copy.deepcopy(self._store.versions.get(version_id))

    def list_by_project(self, project_id: str) -> list[VideoVersion]:
        return [
            copy.deepcopy(v) for v in self._store.versions.values() if v.project_id == project_id
        ]

    def list_by_state(self, state: VersionState) -> list[VideoVersion]:
        found = [v for v in self._store.versions.values() if v.state is state]
        return [copy.deepcopy(v) for v in sorted(found, key=lambda v: (v.created_at, v.version_id))]

    def save(self, version: VideoVersion) -> None:
        if version.version_id not in self._store.versions:
            raise IntegrityViolation(f"저장되지 않은 version_id: {version.version_id}")
        self._store.versions[version.version_id] = copy.deepcopy(version)

    def try_transition(
        self, version_id: str, from_state: VersionState, to_state: VersionState
    ) -> bool:
        stored = self._store.versions.get(version_id)
        if stored is None or stored.state is not from_state:
            return False
        assert_transition(from_state, to_state)
        stored.state = to_state
        return True

    def replace_draft_ir(self, version_id: str, ir: IntermediateRepresentation) -> bool:
        stored = self._store.versions.get(version_id)
        if stored is None or stored.state is not VersionState.DRAFT or stored.is_frozen:
            return False
        stored.ir = ir  # IR 은 불변 값 객체라 복사하지 않아도 된다
        return True


class InMemoryReferenceImageRepository(ReferenceImageRepository):
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def add(self, image: ReferenceImage) -> None:
        self._store.reference_images.setdefault(image.version_id, []).append(copy.deepcopy(image))

    def list_by_version(self, version_id: str) -> list[ReferenceImage]:
        return [copy.deepcopy(i) for i in self._store.reference_images.get(version_id, [])]


class InMemoryTrackRepository(TrackRepository):
    """SCENE_ENTITY 와 같은 의미: IR 의 모든 (장면, 객체) 연결이 행이며, 추적 전에는 PENDING 이다."""

    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def upsert(self, record: TrackRecord) -> None:
        version = self._store.versions.get(record.version_id)
        if version is None or version.ir is None:
            raise IntegrityViolation(f"존재하지 않거나 IR 이 없는 version_id: {record.version_id}")
        scene = (
            version.ir.scene(record.scene_key) if version.ir.has_scene(record.scene_key) else None
        )
        if scene is None or record.entity_key not in scene.entity_keys:  # 무결성 규칙 3
            raise IntegrityViolation(
                f"장면 {record.scene_key} 에 객체 {record.entity_key} 가 연결되어 있지 않습니다"
            )
        self._store.tracks[record.key] = copy.deepcopy(record)

    def list_by_version(self, version_id: str) -> list[TrackRecord]:
        version = self._store.versions.get(version_id)
        if version is None or version.ir is None:
            return []
        records: list[TrackRecord] = []
        for scene in version.ir.scenes:
            for entity_key in scene.entity_keys:
                stored = self._store.tracks.get((version_id, scene.scene_key, entity_key))
                records.append(
                    copy.deepcopy(stored)
                    if stored is not None
                    else TrackRecord(version_id, scene.scene_key, entity_key)
                )
        ir = version.ir
        records.sort(key=lambda r: (ir.scene(r.scene_key).planned_start_ms, r.entity_key))
        return records

    def list_by_entity(self, version_id: str, entity_key: str) -> list[TrackRecord]:
        return [t for t in self.list_by_version(version_id) if t.entity_key == entity_key]


class InMemoryCheckResultRepository(CheckResultRepository):
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def add_all(self, results: Sequence[CheckResult]) -> None:
        for result in results:
            version = self._store.versions.get(result.version_id)
            if version is None:
                raise IntegrityViolation(f"존재하지 않는 version_id: {result.version_id}")
            if result.requirement_key is not None:
                keys = {r.key for r in (version.ir.iter_requirements() if version.ir else [])}
                if result.requirement_key not in keys:
                    raise IntegrityViolation(
                        f"요구조건 {result.requirement_key} 은 버전 {result.version_id} 에 없습니다"
                    )
            self._store.checks.setdefault(result.version_id, []).append(copy.deepcopy(result))

    def list_by_version(self, version_id: str) -> list[CheckResult]:
        return [copy.deepcopy(c) for c in self._store.checks.get(version_id, [])]


class InMemoryUnitOfWork(UnitOfWork):
    def __init__(self, store: InMemoryStore) -> None:
        self.projects = InMemoryProjectRepository(store)
        self.versions = InMemoryVersionRepository(store)
        self.reference_images = InMemoryReferenceImageRepository(store)
        self.tracks = InMemoryTrackRepository(store)
        self.checks = InMemoryCheckResultRepository(store)
        self.committed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:  # 메모리 구현은 스냅샷을 유지하지 않는다 (문서화된 제약)
        pass


def in_memory_uow_factory(store: InMemoryStore | None = None) -> UnitOfWorkFactory:
    shared = store or InMemoryStore()
    return lambda: InMemoryUnitOfWork(shared)
