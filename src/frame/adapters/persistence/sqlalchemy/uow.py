from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from frame.application.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from frame.domain.errors import IntegrityViolation

from .repositories import (
    SqlAlchemyCheckResultRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemyReferenceImageRepository,
    SqlAlchemyTrackRepository,
    SqlAlchemyVersionRepository,
)


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self._session = self._session_factory()
        self.projects = SqlAlchemyProjectRepository(self._session)
        self.versions = SqlAlchemyVersionRepository(self._session)
        self.reference_images = SqlAlchemyReferenceImageRepository(self._session)
        self.tracks = SqlAlchemyTrackRepository(self._session)
        self.checks = SqlAlchemyCheckResultRepository(self._session)
        return self

    def commit(self) -> None:
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise IntegrityViolation(f"DB 무결성 제약 위반: {exc.orig}") from exc

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def sqlalchemy_uow_factory(session_factory: Callable[[], Session]) -> UnitOfWorkFactory:
    return lambda: SqlAlchemyUnitOfWork(session_factory)
