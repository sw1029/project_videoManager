"""VIDEO_VERSION 상태 전이 규칙 (무결성 규칙 1 의 Service 측 검증 근거).

STRUCTURING --성공--> DRAFT --접수(조건 고정)--> QUEUED --> PROCESSING --> COMPLETED
     |                                             |    ^         |
     |                                             |    +---------+ 워커 유실 뒤 재시도 (재시작 복구)
     +--실행 오류--> FAILED <-------------------------+--------------+
"""

from .enums import VersionState as S
from .errors import InvalidStateTransition

TRANSITIONS: dict[S, frozenset[S]] = {
    S.STRUCTURING: frozenset({S.DRAFT, S.FAILED}),
    S.DRAFT: frozenset({S.QUEUED}),
    S.QUEUED: frozenset({S.PROCESSING, S.FAILED}),
    # PROCESSING -> QUEUED: 워커 유실 뒤 재시도 (재시작 복구, ADR-0013)
    S.PROCESSING: frozenset({S.COMPLETED, S.FAILED, S.QUEUED}),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset(),
}

GENERATABLE_STATES: frozenset[S] = frozenset({S.DRAFT})
REVISABLE_STATES: frozenset[S] = frozenset({S.DRAFT, S.COMPLETED, S.FAILED})
TERMINAL_STATES: frozenset[S] = frozenset({S.COMPLETED, S.FAILED})


def can_transition(src: S, dst: S) -> bool:
    return dst in TRANSITIONS[src]


def assert_transition(src: S, dst: S) -> None:
    if not can_transition(src, dst):
        raise InvalidStateTransition(
            f"상태 전이 {src} -> {dst} 는 허용되지 않습니다", from_state=src, to_state=dst
        )
