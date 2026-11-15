"""외부 HTTP API 를 부르는 어댑터의 전송 계층 (표준 라이브러리 urllib, 새 의존성 없음).

어댑터는 HttpTransport 만 보고, 테스트는 가짜 전송을 주입해 네트워크 없이 요청 형식·오류 처리를 검증한다.
- HTTP 오류 상태(4xx·5xx)도 예외가 아니라 응답으로 돌려준다. 상태별 처리는 어댑터가 정한다.
- 응답을 받지 못한 경우(연결 실패·시간 초과)만 TransportError 다.
- Authorization 같은 비밀 헤더는 리다이렉트 요청에 싣지 않는다.
  (urllib 은 기본적으로 모든 헤더를 다른 호스트에도 넘긴다)
- http·https 주소만 부른다 (urllib 은 file:·ftp: 도 연다).
- 받는 양과 시간에 상한이 있다: 응답 본문은 max_response_bytes 까지, 본문을 다 받는 시간은 요청 시간 제한의
  total_timeout_factor 배까지다. 요청의 시간 제한은 읽기 한 번마다 재므로, 조금씩 계속 오는 응답은
  그것만으로는 끊기지 않는다.
- 오류 문구에 헤더 값을 싣지 않는다: 프로토콜이 거절한 헤더 값(키에 줄바꿈이 낀 경우 등)은 표준 라이브러리의
  오류 문구에 통째로 들어 있다.

키를 실어 보낼 주소의 판정(secure_base_url)도 여기 있다: https 만, 로컬 시험 서버는 http 도 받는다.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "LOCAL_HOSTS",
    "MAX_RESPONSE_BYTES",
    "SECRET_HEADERS",
    "HttpResponse",
    "HttpTransport",
    "TransportError",
    "UrllibTransport",
    "secure_base_url",
]

SECRET_HEADERS = frozenset({"authorization", "x-api-key"})
MAX_RESPONSE_BYTES = 512 * 1024 * 1024  # 장면 클립 하나는 수십 MB 다
_CHUNK_BYTES = 64 * 1024
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})  # 로컬 시험 서버 (이 컴퓨터)


def secure_base_url(base_url: str) -> str | None:
    """키를 실어 보내도 되는 API 주소면 다듬은 주소(앞뒤 공백과 끝의 / 를 뗀 것), 아니면 None.

    https 만 받는다 - 키를 평문으로 보내지 않기 위해서다. 로컬 시험 서버(LOCAL_HOSTS)는 http 도 받는다.
    거절하는 문구는 부르는 쪽이 정한다 (어느 설정을 고쳐야 하는지는 부르는 쪽이 안다).
    """
    url = base_url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.netloc:
        return url
    if parts.scheme == "http" and parts.hostname in LOCAL_HOSTS:
        return url
    return None


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
    def __init__(
        self,
        user_agent: str = "FRAME/0.1",
        *,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        total_timeout_factor: float = 10.0,
    ) -> None:
        self.user_agent = user_agent
        self.max_response_bytes = max_response_bytes
        # 본문을 다 받는 시간의 상한 = 요청의 시간 제한 x 이 값
        self.total_timeout_factor = total_timeout_factor

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 60.0,
    ) -> HttpResponse:
        scheme = urlsplit(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise TransportError(
                f"http·https 주소만 부릅니다 (받은 주소의 형식: {scheme or '없음'})"
            )
        deadline = time.monotonic() + timeout * self.total_timeout_factor
        try:
            req = urllib.request.Request(url, data=body, method=method)  # noqa: S310 - 위에서 확인
            req.add_header("User-Agent", self.user_agent)
            for name, value in (headers or {}).items():
                if name.lower() in SECRET_HEADERS:
                    req.add_unredirected_header(name, value)  # 리다이렉트 대상으로 넘어가지 않는다
                else:
                    req.add_header(name, value)
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 위에서 확인
                data = self._read(resp, deadline, timeout)
                return HttpResponse(resp.status, data, _lower(resp.headers.items()))
        except urllib.error.HTTPError as exc:
            return HttpResponse(
                exc.code,
                self._read_what_there_is(exc.fp, deadline, timeout),
                _lower(exc.headers.items() if exc.headers else ()),
            )
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise TransportError(f"{type(exc).__name__}: {reason}") from exc
        except ValueError:
            # 프로토콜이 받지 않는 헤더 값·주소다. 표준 라이브러리의 문구에는 그 값이 통째로 들어 있다
            # (Authorization 헤더면 키다). 원래 오류를 잇지 않아 로그에도 남지 않게 한다
            raise TransportError(
                "요청을 보낼 수 없습니다: 헤더나 주소에 쓸 수 없는 글자가 있습니다"
            ) from None

    def _read(self, resp: Any, deadline: float, timeout: float) -> bytes:
        """본문을 조각으로 읽는다. 양이나 시간의 상한을 넘으면 TransportError."""
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = resp.read1(_CHUNK_BYTES)  # 온 만큼만 읽는다 (다 찰 때까지 기다리지 않는다)
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > self.max_response_bytes:
                raise TransportError(f"응답이 너무 큽니다 ({self.max_response_bytes} 바이트 초과)")
            if time.monotonic() > deadline:
                raise TransportError(
                    f"응답을 {timeout * self.total_timeout_factor:g}초 안에 다 받지 못했습니다"
                )
            chunks.append(chunk)

    def _read_what_there_is(self, fp: Any, deadline: float, timeout: float) -> bytes:
        """오류 응답의 본문. 다 읽지 못하면 (끊김·너무 큼·너무 느림) 빈 본문으로 둔다.

        오류 상태는 예외가 아니라 응답으로 돌려주는 것이 이 계층의 계약이다.
        """
        if fp is None:
            return b""
        try:
            return self._read(fp, deadline, timeout)
        except (TransportError, http.client.HTTPException, OSError):
            return b""


def _lower(items: Any) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in items}
