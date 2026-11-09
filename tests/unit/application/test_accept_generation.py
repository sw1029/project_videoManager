"""생성 접수: DRAFT -> QUEUED 원자적 전이 + 조건 고정 + 작업 디스패치 (무결성 규칙 1)."""

import pytest

from frame.application.dto import CreateProjectCommand
from frame.application.jobs import RUN_GENERATION_PIPELINE
from frame.domain.enums import VersionState
from frame.domain.errors import NotFound, VersionNotGeneratable
from tests.support.factories import CAFE_PROMPT, version_of


def test_accept_moves_draft_to_queued_and_freezes_conditions(container, draft, dispatcher):
    result = container.accept_generation.execute(draft.version_id)

    version = version_of(container, draft.version_id)
    assert result.state is VersionState.QUEUED
    assert version.state is VersionState.QUEUED
    assert version.frozen_fingerprint == version.ir.fingerprint()
    assert [j.name for j in dispatcher.pending] == [RUN_GENERATION_PIPELINE]
    assert dispatcher.pending[0].payload == {"version_id": draft.version_id}


def test_second_accept_is_rejected_with_409_code(container, draft):
    container.accept_generation.execute(draft.version_id)
    with pytest.raises(VersionNotGeneratable) as excinfo:
        container.accept_generation.execute(draft.version_id)
    assert excinfo.value.code == "VERSION_NOT_GENERATABLE"
    assert version_of(container, draft.version_id).state is VersionState.QUEUED


def test_accept_is_rejected_while_structuring(container):
    created = container.create_project.execute(CreateProjectCommand("카페", CAFE_PROMPT))
    with pytest.raises(VersionNotGeneratable):
        container.accept_generation.execute(created.version_id)


def test_accept_unknown_version_is_not_found(container):
    with pytest.raises(NotFound):
        container.accept_generation.execute("ver_missing")
