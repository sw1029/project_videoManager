"""Unit of Work: 한 유스케이스의 저장 작업을 하나의 트랜잭션 경계로 묶는다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from types import TracebackType

from .repositories import (
    CheckResultRepository,
    ProjectRepository,
    ReferenceImageRepository,
    TrackRepository,
    VersionRepository,
)


class UnitOfWork(ABC):
    projects: ProjectRepository
    versions: VersionRepository
    reference_images: ReferenceImageRepository
    tracks: TrackRepository
    checks: CheckResultRepository

    def __enter__(self) -> UnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.rollback()
        self.close()

    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None: ...

    def close(self) -> None:  # noqa: B027 - 기본 구현은 아무것도 하지 않는다
        pass


UnitOfWorkFactory = Callable[[], UnitOfWork]
