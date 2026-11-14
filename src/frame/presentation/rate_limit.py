"""공개 데모용 요청 제한. 클라이언트 IP 별로 최근 60초 동안의 변경 요청(POST·PUT) 수를 센다.

FRAME_RATE_LIMIT_PER_MINUTE 가 0 이면 꺼진다(기본). 리버스 프록시 뒤에서는 uvicorn --proxy-headers 로
실제 클라이언트 IP 가 request.client 에 들어오게 해야 한다.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Request

from .errors import RateLimitExceeded

WINDOW_S = 60.0


class SlidingWindowRateLimiter:
    def __init__(self, limit_per_minute: int, now: Callable[[], float] = time.monotonic) -> None:
        self._limit = limit_per_minute
        self._now = now
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> int | None:
        """허용되면 None, 제한되면 다시 시도할 수 있을 때까지 남은 초를 돌려준다."""
        now = self._now()
        with self._lock:
            window = self._hits[key]
            while window and now - window[0] >= WINDOW_S:
                window.popleft()
            if len(window) >= self._limit:
                return max(1, math.ceil(WINDOW_S - (now - window[0])))
            window.append(now)
            return None


def enforce_rate_limit(request: Request) -> None:
    limiter: SlidingWindowRateLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return
    client = request.client.host if request.client else "unknown"
    retry_after = limiter.hit(client)
    if retry_after is not None:
        raise RateLimitExceeded(retry_after)
