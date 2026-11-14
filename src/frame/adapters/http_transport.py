"""옛 경로 (호환용). 구현은 frame.adapters.shared.http 에 있다 — 새 코드는 그쪽을 import 한다 (ADR-0031).

작업 중인 브랜치가 이 경로를 쓰고 있어 남겨 둔다. 그 브랜치들이 dev 에 들어간 뒤 지운다.
"""

from frame.adapters.shared.http import (
    MAX_RESPONSE_BYTES,
    SECRET_HEADERS,
    HttpResponse,
    HttpTransport,
    TransportError,
    UrllibTransport,
)

__all__ = [
    "MAX_RESPONSE_BYTES",
    "SECRET_HEADERS",
    "HttpResponse",
    "HttpTransport",
    "TransportError",
    "UrllibTransport",
]
