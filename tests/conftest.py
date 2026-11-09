from __future__ import annotations

import pytest

from frame.config import Container
from tests.support.factories import cafe_ir, create_draft, dispatcher_of, make_container


@pytest.fixture
def ir():
    return cafe_ir()


@pytest.fixture
def container() -> Container:
    return make_container()


@pytest.fixture
def dispatcher(container):
    return dispatcher_of(container)


@pytest.fixture
def draft(container):
    """구조화가 끝난 DRAFT 버전 (CreateProjectResult)."""
    return create_draft(container)
