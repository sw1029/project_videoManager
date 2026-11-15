"""버려진 생성 작업의 신호 (ADR-0012, 감사 A-34): 시간 제한으로 호출한 쪽이 먼저 돌아간 작업 스레드는
과금되는 일을 새로 시작하지 않는다.
"""

from __future__ import annotations

import threading
import time

import pytest

from frame.adapters.generator.abandonment import (
    enter_worker,
    is_abandoned,
    pause_unless_abandoned,
    stop_if_abandoned,
)
from frame.application.errors import GenerationTimeout

WAIT = 5.0  # 테스트가 멈춰 버리지 않게 하는 안전 상한


def _in_worker(signal: threading.Event, action):
    """signal 을 받은 작업 스레드에서 action 을 돌리고 그 결과(값이나 예외)를 돌려준다."""
    outcome: dict[str, object] = {}

    def work() -> None:
        enter_worker(signal)
        try:
            outcome["value"] = action()
        except BaseException as exc:  # noqa: BLE001 - 스레드의 예외를 단언으로 옮긴다
            outcome["error"] = exc

    worker = threading.Thread(target=work)
    worker.start()
    worker.join(WAIT)
    assert not worker.is_alive()
    return outcome


def test_outside_a_time_limited_worker_nothing_is_ever_abandoned():
    assert is_abandoned() is False
    stop_if_abandoned("제출하지 않습니다")  # 아무 일도 없다
    pause_unless_abandoned(0, "다시 시도하지 않습니다")


def test_a_worker_goes_on_until_its_caller_gives_up():
    signal = threading.Event()

    assert _in_worker(signal, is_abandoned) == {"value": False}
    assert _in_worker(signal, lambda: stop_if_abandoned("제출하지 않습니다")) == {"value": None}

    signal.set()  # 마감이 지나 호출한 쪽이 돌아갔다
    assert _in_worker(signal, is_abandoned) == {"value": True}


def test_an_abandoned_worker_is_stopped_before_it_starts_something_billable():
    signal = threading.Event()
    signal.set()

    error = _in_worker(signal, lambda: stop_if_abandoned("제출하지 않습니다"))["error"]

    assert isinstance(error, GenerationTimeout) and error.retryable is False
    assert "버려진" in str(error) and "제출하지 않습니다" in str(error)


def test_a_pause_ends_at_once_when_the_worker_is_abandoned_meanwhile():
    signal = threading.Event()
    threading.Timer(0.05, signal.set).start()
    started = time.monotonic()

    error = _in_worker(signal, lambda: pause_unless_abandoned(WAIT, "다시 시도하지 않습니다"))[
        "error"
    ]

    assert isinstance(error, GenerationTimeout) and error.retryable is False
    assert time.monotonic() - started < WAIT / 2  # 끝까지 기다리지 않았다


def test_a_pause_that_is_not_interrupted_just_waits():
    signal = threading.Event()
    started = time.monotonic()

    assert _in_worker(signal, lambda: pause_unless_abandoned(0.05, "x")) == {"value": None}
    assert time.monotonic() - started >= 0.04


def test_the_signal_belongs_to_the_worker_thread_only():
    signal = threading.Event()
    signal.set()
    _in_worker(signal, is_abandoned)

    assert is_abandoned() is False  # 다른 스레드(이 테스트 스레드)는 버려진 것이 아니다
    with pytest.raises(GenerationTimeout):
        raise _in_worker(signal, lambda: stop_if_abandoned("x"))["error"]
