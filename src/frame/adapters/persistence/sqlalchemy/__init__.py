"""저장소와 저장 단위의 SQLAlchemy 구현 (운영 MySQL 8.4, 테스트·시연 SQLite, ADR-0004).

맡는 개념
    도메인 객체를 행으로 저장하고 다시 읽는다. 도메인 객체와 행 사이의 변환은 repositories 에서만 일어난다.

밖에서 쓰는 이름
    create_engine_from_url    DB 주소로 엔진을 만든다 (SQLite 는 WAL · 잠금 대기, in-memory 는 한 연결)
    make_session_factory      세션 팩토리 (in-memory SQLite 는 세션을 한 번에 하나만 연다)
    create_schema             테이블을 만든다
    SqlAlchemyUnitOfWork · sqlalchemy_uow_factory    저장 단위

모듈 지도
    engine          엔진과 세션 팩토리
    models          테이블 (ERD 와 대응)
    repositories    저장소 구현 (Data Mapper)
    uow             저장 단위 (세션 하나로 저장소들을 묶는다)
"""

from .engine import create_engine_from_url, create_schema, make_session_factory
from .uow import SqlAlchemyUnitOfWork, sqlalchemy_uow_factory

__all__ = [
    "SqlAlchemyUnitOfWork",
    "create_engine_from_url",
    "create_schema",
    "make_session_factory",
    "sqlalchemy_uow_factory",
]
