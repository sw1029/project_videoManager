"""키마다 하나씩 쥐는 잠금 (EpisodeRepository.lock 의 구현). 한 프로세스 안에서만 지킨다.

같은 스레드는 다시 들어갈 수 있다 (유스케이스가 다른 유스케이스를 부른다: 화면 수정 -> 화면 검수).
잠금은 키마다 처음 쓸 때 만들고 지우지 않는다. 키는 편·테마의 수만큼이라 쌓여도 작다.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class KeyLocks:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks.setdefault(key, threading.RLock())
        with lock:
            yield
