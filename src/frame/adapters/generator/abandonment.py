"""버려진 생성 작업의 신호 (ADR-0012): 시간 제한으로 호출한 쪽이 먼저 돌아간 작업 스레드인가.

TimeoutVideoGenerator 는 장면 하나를 작업 스레드에서 만들고, 마감이 지나면 호출한 쪽만 먼저 돌아간다 (버전은 실패).
파이썬 스레드는 강제로 멈출 수 없어서, 버려진 스레드가 스스로 과금되는 일을 새로 시작하지 않아야 한다.
그 약속이 이 모듈이다.

- 작업 스레드를 만드는 쪽: 스레드 안에서 enter_worker(signal) 을 부르고, 마감이 지나면 signal 을 켠다.
- 작업 스레드 안에서 도는 쪽 (재시도 사이의 대기, 외부 API 어댑터): 과금되는 일(제출) 직전에 stop_if_abandoned(),
  기다릴 때는 pause_unless_abandoned() 를 쓴다. 버려졌으면 GenerationTimeout(retryable=False) 으로 멈춘다.
- 시간 제한 밖(신호가 없는 스레드)에서는 아무 일도 하지 않는다.
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar

from frame.application.errors import GenerationTimeout

__all__ = ["enter_worker", "is_abandoned", "pause_unless_abandoned", "stop_if_abandoned"]

# 시간 제한 아래의 작업 스레드 안에서만 값이 있다. 마감이 지나 호출한 쪽이 먼저 돌아가면 켜진다
_SIGNAL: ContextVar[threading.Event | None] = ContextVar("frame_generation_abandoned", default=None)


def enter_worker(signal: threading.Event) -> None:
    """지금 스레드가 시간 제한 아래의 작업 스레드임을 알린다. signal 은 버려질 때 켜진다."""
    _SIGNAL.set(signal)


def is_abandoned() -> bool:
    signal = _SIGNAL.get()
    return signal is not None and signal.is_set()


def stop_if_abandoned(instead: str) -> None:
    """버려진 작업 스레드면 GenerationTimeout 으로 멈춘다. instead 는 하지 않는 일 ("제출하지 않습니다")."""
    if is_abandoned():
        raise _abandoned(instead)


def pause_unless_abandoned(seconds: float, instead: str) -> None:
    """seconds 만큼 기다린다 (0 이면 바로 돌아온다). 버려진 작업 스레드면 기다리던 중에도 바로 멈춘다."""
    signal = _SIGNAL.get()
    if signal is None:
        if seconds > 0:
            time.sleep(seconds)
        return
    if signal.is_set() or (seconds > 0 and signal.wait(seconds)):
        raise _abandoned(instead)


def _abandoned(instead: str) -> GenerationTimeout:
    return GenerationTimeout(f"시간 제한으로 버려진 생성 작업이라 {instead}", retryable=False)
