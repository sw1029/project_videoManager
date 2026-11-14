"""키별 잠금: 같은 키의 일을 한 번에 하나만 하게 한다 (이 프로세스 안에서).

같은 장면을 두 요청이 동시에 제출하거나 같은 클립의 기준을 두 작업이 동시에 뽑는 것을 막는다. 뒤에 온 쪽은
앞선 쪽이 끝난 뒤 그 결과(생성 캐시, 색인)를 쓴다. 프로세스가 여럿이면 저마다의 잠금이라 서로를 막지 못한다.

키별 잠금의 구현은 이것 하나다 (감사 P-20). 쥔 스레드가 같은 키로 다시 들어가야 하는 쪽은 reentrant=True 로 만든다:
편 · 테마 문서의 잠금이 그렇다 (유스케이스가 그 키를 쥔 채, 같은 키를 쥐는 다른 유스케이스를 부른다).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeAlias

__all__ = ["KeyedLocks"]


# 실행 시점의 threading.Lock · RLock 은 함수라 | 로 묶을 수 없다. 형 검사기만 읽도록 글로 적는다
_Lock: TypeAlias = "threading.Lock | threading.RLock"


class KeyedLocks:
    """키별 잠금 (같은 키의 작업을 한 번에 하나만). 쓰는 쪽이 없어지면 잠금도 지운다.

    reentrant=True 면 그 키를 쥐고 있는 스레드는 같은 키로 다시 들어간다. 다른 스레드는 그대로 기다린다.
    """

    def __init__(self, *, reentrant: bool = False) -> None:
        self._guard = threading.Lock()
        self._reentrant = reentrant
        self._locks: dict[str, tuple[_Lock, list[int]]] = {}

    @property
    def in_use(self) -> int:
        """지금 누군가 쥐었거나 기다리고 있는 키의 수."""
        with self._guard:
            return len(self._locks)

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

    def _enter(self, key: str) -> _Lock:
        with self._guard:
            if key not in self._locks:
                self._locks[key] = (self._new_lock(), [0])
            lock, users = self._locks[key]
            users[0] += 1
        return lock

    def _new_lock(self) -> _Lock:
        return threading.RLock() if self._reentrant else threading.Lock()

    def _leave(self, key: str) -> None:
        with self._guard:
            users = self._locks[key][1]
            users[0] -= 1
            if users[0] == 0:
                del self._locks[key]
