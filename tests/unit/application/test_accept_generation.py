"""생성 접수: DRAFT -> QUEUED 원자적 전이 + 조건 고정 + 작업 디스패치 (무결성 규칙 1)."""

from dataclasses import replace

import pytest

from frame.application.dto import CreateProjectCommand
from frame.application.jobs import RUN_GENERATION_PIPELINE
from frame.domain.enums import EntityKind, VersionState
from frame.domain.errors import NotFound, ValidationError, VersionNotGeneratable
from frame.domain.ir import EntitySpec
from tests.support.clock import ManualClock
from tests.support.factories import (
    CAFE_PROMPT,
    cafe_ir,
    create_draft,
    make_container,
    save_unchecked_draft,
    version_of,
    with_scene_key,
)


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


def test_accept_records_when_the_version_was_queued():
    """#2: 접수 시각을 남긴다 (복구·운영 관찰용). 아직 실행 전이라 시작 시각·시도 횟수는 비어 있다."""
    clock = ManualClock()
    container = make_container(clock=clock)
    draft = create_draft(container)
    queued_at = clock.advance(minutes=1)

    container.accept_generation.execute(draft.version_id)

    version = version_of(container, draft.version_id)
    assert version.queued_at == queued_at
    assert (version.started_at, version.attempt) == (None, 0)


# ---- 키 규칙이 생기기 전에 저장된 행 (감사 P-20 · 델타 B10-D02 · D06) ----
def _with_twin_entity(ir):
    """객체 키가 대소문자만 다른 IR (cup · Cup)."""
    return replace(ir, entities=(*ir.entities, EntitySpec("Cup", EntityKind.PROP)))


@pytest.mark.parametrize(
    ("change", "words"),
    [
        (lambda ir: with_scene_key(ir, "intro", "../ver_victim/intro"), "장면 키"),
        (lambda ir: with_scene_key(ir, "closeup", "Intro"), "대소문자"),
        (_with_twin_entity, "대소문자"),
    ],
    ids=["points_elsewhere", "scene_case_only", "entity_case_only"],
)
def test_a_version_saved_before_the_key_rules_is_not_accepted(container, dispatcher, change, words):
    """키 규칙에 어긋난 IR 은 접수하지 않는다 — 생성기를 부르기 전에 막는다 (규칙 #10 전에 저장된 행).

    객체 · 장면 키는 클립과 기준 이미지의 저장 키에 그대로 들어간다. 접수하고 나면: 다른 자리를 가리키는 키는
    저장소가 막지만 클립을 쓸 때라 이미 생성기를 부른 뒤이고 (실제 생성기는 유료 호출을 마쳤다), 대소문자만 다른
    키는 저장소가 막지 못해 대소문자를 가리지 않는 파일 시스템에서 두 장면의 클립이 한 파일이 된다
    (버전은 COMPLETED 다).
    """
    version_id = save_unchecked_draft(container, change(cafe_ir()))

    with pytest.raises(VersionNotGeneratable, match=words) as raised:
        container.accept_generation.execute(version_id)

    assert raised.value.code == "VERSION_NOT_GENERATABLE"
    assert version_of(container, version_id).state is VersionState.DRAFT
    assert dispatcher.pending == []


def test_a_version_that_breaks_only_other_rules_is_still_accepted(container):
    """접수가 다시 보는 것은 키뿐이다. 다른 규칙으로는 옛 행을 막지 않는다 (감사 A-20 과 같은 판단)."""
    ir = cafe_ir()
    shifted = replace(ir, scenes=(replace(ir.scenes[0], planned_start_ms=100), *ir.scenes[1:]))
    with pytest.raises(ValidationError):
        shifted.validate()  # 첫 장면이 0 ms 에서 시작하지 않는다
    version_id = save_unchecked_draft(container, shifted)

    assert container.accept_generation.execute(version_id).state is VersionState.QUEUED
