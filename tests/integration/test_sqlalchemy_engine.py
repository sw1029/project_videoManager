"""SQLAlchemy 엔진 설정 (감사 A-14): SQLite 파일 DB 를 여러 스레드가 함께 쓸 때.

요청 스레드, 작업 스레드, 진행 기록, 주기 복구가 같은 파일에 쓴다. 기본 설정(롤백 저널)이면 쓰는 동안 읽는 쪽도
막히고, 쓰기가 겹치면 'database is locked' 로 실패한다.
"""

from __future__ import annotations

from frame.adapters.persistence.sqlalchemy import create_engine_from_url


def _pragma(engine, name: str):
    with engine.connect() as connection:
        return connection.exec_driver_sql(f"PRAGMA {name}").scalar()


def test_a_file_database_lets_readers_in_while_someone_writes(tmp_path):
    engine = create_engine_from_url(f"sqlite+pysqlite:///{(tmp_path / 'frame.db').as_posix()}")
    try:
        assert _pragma(engine, "journal_mode") == "wal"
        assert _pragma(engine, "busy_timeout") == 30_000  # 겹친 쓰기는 이만큼(ms) 기다린다
        assert _pragma(engine, "foreign_keys") == 1
    finally:
        engine.dispose()


def test_every_connection_of_the_pool_gets_the_same_settings(tmp_path):
    engine = create_engine_from_url(f"sqlite+pysqlite:///{(tmp_path / 'frame.db').as_posix()}")
    try:
        with engine.connect() as first, engine.connect() as second:
            for connection in (first, second):
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30_000
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        engine.dispose()


def test_the_in_memory_database_is_left_as_it_is():
    """연결 하나가 곧 DB 다. 저널 방식을 바꿀 것이 없고, 겹친 쓰기는 세션을 하나씩 여는 것으로 막는다 (A-41)."""
    engine = create_engine_from_url("sqlite+pysqlite:///:memory:")
    try:
        assert _pragma(engine, "journal_mode") == "memory"
        assert _pragma(engine, "foreign_keys") == 1
    finally:
        engine.dispose()
