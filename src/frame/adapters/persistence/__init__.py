"""저장소와 저장 단위 (Repository · UnitOfWork 포트, ADR-0004).

밖에서 쓰는 이름 - 이 패키지는 이름을 내보내지 않는다. 구현의 모듈에서 import 한다.

모듈 지도
    memory         메모리 구현 (테스트 · 기본 설정): InMemoryUnitOfWork, in_memory_uow_factory
    sqlalchemy/    SQLAlchemy 구현 (운영 MySQL 8.4, 테스트 · 시연 SQLite)
"""

__all__: list[str] = []
