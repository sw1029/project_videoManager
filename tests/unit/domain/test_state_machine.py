"""VIDEO_VERSION 상태 전이 규칙 (무결성 규칙 1 의 근거)."""

from datetime import UTC, datetime

import pytest

from frame.domain.enums import VersionState as S
from frame.domain.errors import InvalidStateTransition
from frame.domain.models import VideoVersion
from frame.domain.state_machine import (
    GENERATABLE_STATES,
    TRANSITIONS,
    assert_transition,
    can_transition,
)


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (S.STRUCTURING, S.DRAFT),
        (S.STRUCTURING, S.FAILED),
        (S.DRAFT, S.QUEUED),
        (S.QUEUED, S.PROCESSING),
        (S.QUEUED, S.FAILED),
        (S.PROCESSING, S.COMPLETED),
        (S.PROCESSING, S.FAILED),
        (S.PROCESSING, S.QUEUED),  # 워커 유실 뒤 재시도 (재시작 복구, ADR-0013)
    ],
)
def test_allowed_transitions(src, dst):
    assert can_transition(src, dst)
    assert_transition(src, dst)  # 예외 없음


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (S.DRAFT, S.PROCESSING),
        (S.DRAFT, S.COMPLETED),
        (S.STRUCTURING, S.QUEUED),
        (S.QUEUED, S.COMPLETED),
        (S.COMPLETED, S.DRAFT),
        (S.FAILED, S.QUEUED),
    ],
)
def test_forbidden_transitions(src, dst):
    assert not can_transition(src, dst)
    with pytest.raises(InvalidStateTransition) as excinfo:
        assert_transition(src, dst)
    assert excinfo.value.code == "INVALID_STATE_TRANSITION"


def test_terminal_states_have_no_outgoing_transitions():
    assert TRANSITIONS[S.COMPLETED] == frozenset()
    assert TRANSITIONS[S.FAILED] == frozenset()


def test_only_draft_accepts_generation():
    assert GENERATABLE_STATES == frozenset({S.DRAFT})


def _version(state: S) -> VideoVersion:
    return VideoVersion("ver_1", "prj_1", state, datetime(2026, 1, 1, tzinfo=UTC))


def test_entity_rejects_invalid_transition_and_keeps_state():
    version = _version(S.DRAFT)
    with pytest.raises(InvalidStateTransition):
        version.transition_to(S.COMPLETED)
    assert version.state is S.DRAFT
    version.transition_to(S.QUEUED)
    assert version.state is S.QUEUED


def test_mark_failed_records_error_code_and_message():
    version = _version(S.PROCESSING)
    version.mark_failed("GENERATION_ERROR", "GPU 메모리 부족")
    assert version.state is S.FAILED
    assert (version.error_code, version.error_message) == ("GENERATION_ERROR", "GPU 메모리 부족")
