"""외부 HTTP API 를 부르는 어댑터의 전송 계층 (표준 라이브러리 urllib, 새 의존성 없음).

어댑터는 HttpTransport 만 보고, 테스트는 가짜 전송을 주입해 네트워크 없이 요청 형식·오류 처리를 검증한다.
- HTTP 오류 상태(4xx·5xx)도 예외가 아니라 응답으로 돌려준다. 상태별 처리는 어댑터가 정한다.
- 응답을 받지 못한 경우(연결 실패·시간 초과)만 TransportError 다.
- Authorization 같은 비밀 헤더는 리다이렉트 요청에 싣지 않는다.
  (urllib 은 기본적으로 모든 헤더를 다른 호스트에도 넘긴다)
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

SECRET_HEADERS = frozenset({"authorization", "x-api-key"})


class TransportError(Exception):
    """응답을 받지 못함 (연결 실패·DNS·시간 초과). 요청이 서버에 닿았는지는 알 수 없다."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)  # 이름은 소문자

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class HttpTransport(ABC):
    @abstractmethod
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 60.0,
    ) -> HttpResponse:
        """HTTP 상태와 관계없이 응답을 돌려준다. 응답을 받지 못했을 때만 TransportError."""


class UrllibTransport(HttpTransport):
    def __init__(self, user_agent: str = "FRAME/0.1") -> None:
        self.user_agent = user_agent

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 60.0,
    ) -> HttpResponse:
        # 주소는 부르는 어댑터가 검증한 것만 온다 (OpenRouter 는 https 만: _checked_base_url)
        req = urllib.request.Request(url, data=body, method=method)  # noqa: S310
        req.add_header("User-Agent", self.user_agent)
        for name, value in (headers or {}).items():
            if name.lower() in SECRET_HEADERS:
                req.add_unredirected_header(name, value)  # 리다이렉트 대상으로 넘어가지 않는다
            else:
                req.add_header(name, value)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return HttpResponse(resp.status, resp.read(), _lower(resp.headers.items()))
        except urllib.error.HTTPError as exc:
            try:
                data = exc.read()
            except OSError:
                data = b""
            return HttpResponse(exc.code, data, _lower(exc.headers.items() if exc.headers else ()))
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise TransportError(f"{type(exc).__name__}: {reason}") from exc


def _lower(items: Any) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in items}
