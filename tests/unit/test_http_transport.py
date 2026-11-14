"""UrllibTransport: 오류 상태도 응답으로 돌려주고, Authorization 헤더는 리다이렉트로 넘기지 않는다.

루프백(127.0.0.1) 서버만 쓴다.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
import time

import pytest

from frame.adapters.http_transport import TransportError, UrllibTransport


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server 규약
        self.server.seen.append((self.path, self.headers.get("Authorization")))
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/big"):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(200_000))
            self.end_headers()
            self.wfile.write(b"v" * 200_000)
            return
        if self.path.startswith("/broken-error"):
            # 오류 응답의 본문이 약속한 길이보다 일찍 끊긴다
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"bad gateway")
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        if self.path.startswith("/dripping"):
            # 한 번에 조금씩, 끝나지 않을 것처럼 보낸다 (조각 사이의 간격은 요청의 시간 제한보다 짧다)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"v")
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass  # 받는 쪽이 그만 받았다
            return
        if self.path.startswith("/limited"):
            self._send(
                429,
                {"error": {"code": 429, "message": "Rate limit exceeded"}},
                {"Retry-After": "7"},
            )
            return
        self._send(200, {"ok": True})

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.seen.append((self.path, self.headers.get("Authorization"), body))
        self._send(202, {"received": json.loads(body), "type": self.headers.get("Content-Type")})

    def _send(self, code, payload, headers=None):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # 테스트 출력을 조용히
        pass


def _serve():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.seen = []
    server.redirect_to = ""
    # 기본 poll 간격(0.5초)이면 서버를 닫을 때마다 그만큼 기다린다 (감사 T-10)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    return server


@pytest.fixture
def servers():
    started = [_serve(), _serve()]
    yield started
    for server in started:
        server.shutdown()
        server.server_close()


def _url(server, path):
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def test_error_statuses_come_back_as_responses(servers):
    response = UrllibTransport().request("GET", _url(servers[0], "/limited"), timeout=5)
    assert response.status == 429
    assert response.json()["error"]["message"] == "Rate limit exceeded"
    assert response.headers["retry-after"] == "7"
    assert response.content_type == "application/json"


def test_post_sends_json_body_and_headers(servers):
    response = UrllibTransport().request(
        "POST",
        _url(servers[0], "/videos"),
        headers={"Authorization": "Bearer sk-or-v1-test", "Content-Type": "application/json"},
        body=b'{"model": "m"}',
        timeout=5,
    )
    assert response.status == 202
    assert response.json() == {"received": {"model": "m"}, "type": "application/json"}
    assert servers[0].seen == [("/videos", "Bearer sk-or-v1-test", b'{"model": "m"}')]


def test_authorization_is_not_forwarded_to_a_redirect_target(servers):
    origin, other = servers
    origin.redirect_to = _url(other, "/landing")
    response = UrllibTransport().request(
        "GET",
        _url(origin, "/redirect"),
        headers={"Authorization": "Bearer sk-or-v1-secret"},
        timeout=5,
    )
    assert response.status == 200
    assert origin.seen == [("/redirect", "Bearer sk-or-v1-secret")]
    assert other.seen == [("/landing", None)]  # 다른 서버로는 키가 가지 않는다


def test_connection_failure_raises_transport_error():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]  # 닫힌 포트
    with pytest.raises(TransportError):
        UrllibTransport().request("GET", f"http://127.0.0.1:{port}/", timeout=3)


# ---- 밖으로 나가는 값과 받는 양의 상한 (감사 A-38) -----------------------------------------


def test_a_header_value_the_protocol_refuses_never_shows_up_in_the_error(servers):
    """키 가운데에 줄바꿈이 끼면 http.client 가 'Invalid header value b"Bearer sk-or-..."' 로 거절한다.

    그 오류가 그대로 올라가면 버전의 error_message 와 서버 로그에 키 전체가 남는다.
    """
    secret = "sk-or-v1-secret\nsecond-half"

    with pytest.raises(TransportError) as raised:
        UrllibTransport().request(
            "GET", _url(servers[0], "/ok"), headers={"Authorization": f"Bearer {secret}"}, timeout=5
        )

    assert "sk-or" not in str(raised.value) and "second-half" not in str(raised.value)
    assert (
        raised.value.__cause__ is None and raised.value.__suppress_context__
    )  # 로그에도 남지 않게
    assert servers[0].seen == []  # 요청은 나가지 않았다


@pytest.mark.parametrize("url", ["file:///etc/hosts", "ftp://127.0.0.1/x", "127.0.0.1/x", ""])
def test_only_http_addresses_are_requested(url):
    """urllib 은 file:·ftp: 도 연다. 주소가 설정이나 응답에서 올 수 있으므로 여기서 한 번 더 막는다."""
    with pytest.raises(TransportError, match="http"):
        UrllibTransport().request("GET", url, timeout=5)


def test_a_response_larger_than_the_limit_is_refused(servers):
    transport = UrllibTransport(max_response_bytes=50_000)

    with pytest.raises(TransportError, match="50000"):
        transport.request("GET", _url(servers[0], "/big"), timeout=5)

    assert (
        len(UrllibTransport().request("GET", _url(servers[0], "/big"), timeout=5).body) == 200_000
    )


def test_an_error_body_that_breaks_off_still_comes_back_as_a_response(servers):
    """오류 상태는 예외가 아니라 응답이다. 본문을 다 못 읽었다고 그 계약이 바뀌지 않는다."""
    response = UrllibTransport().request("GET", _url(servers[0], "/broken-error"), timeout=5)

    assert response.status == 502 and response.body in (b"", b"bad gateway")


def test_a_response_that_never_ends_is_given_up_on(servers):
    """요청의 시간 제한은 읽기 한 번마다 잰다. 조금씩 계속 오는 응답은 그 제한에 걸리지 않고 끝없이 붙든다."""
    transport = UrllibTransport(total_timeout_factor=2)
    started = time.monotonic()

    with pytest.raises(TransportError, match="다 받지 못했습니다"):
        transport.request("GET", _url(servers[0], "/dripping"), timeout=0.5)

    assert time.monotonic() - started < 4  # 다 받으려면 5초가 걸린다
