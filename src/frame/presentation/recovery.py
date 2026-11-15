"""주기 복구 스레드 (#2, ADR-0013): FRAME_RECOVERY_INTERVAL_S 마다 리스가 지난 PROCESSING 버전을 회수한다.

서버 기동 훅(app.py 의 lifespan)이 요청을 받기 전에 한 번 복구한 뒤 이 스레드를 띄우고, 종료 때 멈춘다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class RecoveryLoop:
    def __init__(self, task: Callable[[], object], interval_s: float) -> None:
        self._task = task
        self._interval = float(interval_s)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._interval <= 0 or self._thread is not None:
            return  # 0 이면 기동 때만 복구한다
        self._thread = threading.Thread(target=self._run, name="frame-recovery", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._task()
            except Exception:  # noqa: BLE001 - 일시 오류(DB 등)로 스레드가 죽지 않게 한다
                log.exception("주기 복구에 실패했습니다. 다음 주기에 다시 시도합니다")
