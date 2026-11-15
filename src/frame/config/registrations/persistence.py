"""영속성 (UnitOfWork) 의 등록. 팩토리는 저장 단위를 여는 함수(UnitOfWorkFactory)를 돌려준다."""

from __future__ import annotations

from frame.adapters.persistence.memory import InMemoryStore, in_memory_uow_factory
from frame.adapters.persistence.sqlalchemy import (
    create_engine_from_url,
    create_schema,
    make_session_factory,
    sqlalchemy_uow_factory,
)
from frame.application.ports import UnitOfWork, UnitOfWorkFactory

from ..registry import AdapterRegistry, BuildContext


def register(registry: AdapterRegistry) -> None:
    registry.register(UnitOfWork, "memory", lambda ctx: in_memory_uow_factory(InMemoryStore()))
    registry.register(UnitOfWork, "sqlalchemy", _sqlalchemy)


def _sqlalchemy(ctx: BuildContext) -> UnitOfWorkFactory:
    engine = create_engine_from_url(ctx.settings.db_url)
    if ctx.settings.db_url.startswith("sqlite") or ctx.settings.db_create_schema:
        create_schema(engine)  # 운영(MySQL)은 Alembic 마이그레이션으로 스키마를 만든다
    return sqlalchemy_uow_factory(make_session_factory(engine))
