"""Persistence 포트 (Repository 패턴). 구현: 메모리(테스트) / SQLAlchemy(MySQL·SQLite)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from frame.domain.checks import CheckResult
from frame.domain.enums import VersionState
from frame.domain.models import Project, ReferenceImage, VideoVersion
from frame.domain.tracking import TrackRecord


class ProjectRepository(ABC):
    @abstractmethod
    def add(self, project: Project) -> None: ...

    @abstractmethod
    def get(self, project_id: str) -> Project | None: ...


class VersionRepository(ABC):
    @abstractmethod
    def add(self, version: VideoVersion) -> None: ...

    @abstractmethod
    def get(self, version_id: str) -> VideoVersion | None: ...

    @abstractmethod
    def list_by_project(self, project_id: str) -> list[VideoVersion]: ...

    @abstractmethod
    def list_by_state(self, state: VersionState) -> list[VideoVersion]:
        """그 상태인 버전 전부 (만든 순). 재시작 복구가 QUEUED·PROCESSING 을 찾을 때 쓴다 (#2)."""

    @abstractmethod
    def save(self, version: VideoVersion) -> None:
        """상태·IR·결과·오류 필드를 저장한다. (IR 은 조건 고정 전까지만 바뀐다)"""

    @abstractmethod
    def try_transition(
        self, version_id: str, from_state: VersionState, to_state: VersionState
    ) -> bool:
        """현재 상태가 from_state 일 때만 to_state 로 바꾼다 (조건부 갱신, 무결성 규칙 1).

        동시에 두 요청이 들어와도 하나만 True 를 받는다.
        """


class ReferenceImageRepository(ABC):
    @abstractmethod
    def add(self, image: ReferenceImage) -> None: ...

    @abstractmethod
    def list_by_version(self, version_id: str) -> list[ReferenceImage]: ...


class TrackRepository(ABC):
    @abstractmethod
    def upsert(self, record: TrackRecord) -> None: ...

    @abstractmethod
    def list_by_version(self, version_id: str) -> list[TrackRecord]: ...

    @abstractmethod
    def list_by_entity(self, version_id: str, entity_key: str) -> list[TrackRecord]: ...


class CheckResultRepository(ABC):
    @abstractmethod
    def add_all(self, results: Sequence[CheckResult]) -> None: ...

    @abstractmethod
    def list_by_version(self, version_id: str) -> list[CheckResult]: ...
