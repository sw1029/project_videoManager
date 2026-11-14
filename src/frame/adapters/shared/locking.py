"""키별 잠금: 같은 키의 일을 한 번에 하나만 하게 한다 (이 프로세스 안에서).

같은 장면을 두 요청이 동시에 제출하거나 같은 클립의 기준을 두 작업이 동시에 뽑는 것을 막는다. 뒤에 온 쪽은
앞선 쪽이 끝난 뒤 그 결과(생성 캐시, 색인)를 쓴다. 프로세스가 여럿이면 저마다의 잠금이라 서로를 막지 못한다.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

__all__ = ["KeyedLocks"]


class KeyedLocks:
    """키별 잠금 (같은 키의 작업을 한 번에 하나만). 쓰는 쪽이 없어지면 잠금도 지운다."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, tuple[threading.Lock, list[int]]] = {}

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        """그 키의 잠금을 얻을 때까지 기다렸다가, 블록이 끝나면 푼다."""
        lock = self._enter(key)
        try:
            with lock:
                yield
        finally:
            self._leave(key)

    @contextmanager
    def try_hold(self, key: str) -> Iterator[bool]:
        """기다리지 않고 얻어 본다. 얻었으면 True 를 내주고 블록이 끝나면 푼다. 다른 쪽이 쥐고 있으면 False 를 내준다.

        남의 키에 잠깐 쓰려는 쪽이 쓴다: 그 키로 누가 일하는 중이면 끼어들지 않고 물러난다.
        """
        lock = self._enter(key)
        acquired = lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()
            self._leave(key)

    def _enter(self, key: str) -> threading.Lock:
        with self._guard:
            lock, users = self._locks.setdefault(key, (threading.Lock(), [0]))
            users[0] += 1
        return lock

    def _leave(self, key: str) -> None:
        with self._guard:
            users = self._locks[key][1]
            users[0] -= 1
            if users[0] == 0:
                del self._locks[key]
