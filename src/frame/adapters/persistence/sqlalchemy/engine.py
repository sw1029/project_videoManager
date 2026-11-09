"""엔진/세션 팩토리. SQLite(테스트)와 MySQL 8.4(운영)를 같은 코드로 다룬다."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


def create_engine_from_url(url: str) -> Engine:
    if url.startswith("sqlite"):
        in_memory = ":memory:" in url or url.rstrip("/").endswith("sqlite://")
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if in_memory else None,
        )

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")  # FK 로 무결성 규칙 3·4·5 를 강제
            cursor.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


def create_schema(engine: Engine) -> None:
    """개발·테스트용. 운영 마이그레이션은 Alembic 으로 관리한다."""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> Callable[[], Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)
