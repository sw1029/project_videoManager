"""엔진/세션 팩토리. SQLite(테스트)와 MySQL 8.4(운영)를 같은 코드로 다룬다.

in-memory SQLite 는 연결 하나가 곧 DB 라서 모든 스레드가 그 연결을 함께 쓴다 (StaticPool).
스레드 디스패처로 돌면 요청 스레드·작업 스레드·주기 복구가 그 연결 위에서 서로의 트랜잭션에 끼어든다.
그래서 이런 엔진의 세션(저장 단위)은 한 번에 하나만 열리게 한다 (감사 A-41).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateColumn

from .models import Base

log = logging.getLogger(__name__)


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
    """개발·테스트용. 운영 마이그레이션은 Alembic 으로 관리한다.

    이미 있는 테이블에 모델의 새 열(널 허용이거나 기본값이 있는 열)이 없으면 ADD COLUMN 으로 더한다.
    예전 SQLite 개발 DB 를 그대로 쓰게 하려는 것이며, 열을 지우거나 형식을 바꾸지는 않는다.
    """
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = CreateColumn(column).compile(dialect=engine.dialect)
                conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {ddl}")
                log.warning("스키마: %s.%s 열을 더했습니다 (예전 DB)", table.name, column.name)


def make_session_factory(engine: Engine) -> Callable[[], Session]:
    if isinstance(engine.pool, StaticPool):
        # 연결이 하나뿐인 엔진: 세션을 한 번에 하나만 연다
        return sessionmaker(
            bind=engine,
            class_=_OneAtATimeSession,
            expire_on_commit=False,
            autoflush=True,
            gate=threading.RLock(),
        )
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)


class _OneAtATimeSession(Session):
    """연결 하나를 모든 스레드가 함께 쓰는 엔진의 세션. 열 때 문을 잠그고 close() 에서 연다.

    저장 단위(UnitOfWork)는 짧게 열고 반드시 닫으므로, 다른 스레드는 그동안만 기다린다. 같은 스레드가 저장 단위
    안에서 또 하나를 여는 것은 막지 않는다 (재진입 잠금).
    """

    def __init__(self, *args: Any, gate: Any, **kwargs: Any) -> None:
        gate.acquire()
        self._gate = gate
        try:
            super().__init__(*args, **kwargs)
        except BaseException:
            self._leave()
            raise

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._leave()

    def _leave(self) -> None:
        gate, self._gate = self._gate, None
        if gate is not None:
            gate.release()
