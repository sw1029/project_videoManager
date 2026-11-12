"""UrllibTransport: 오류 상태도 응답으로 돌려주고, Authorization 헤더는 리다이렉트로 넘기지 않는다.

루프백(127.0.0.1) 서버만 쓴다.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading

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
    threading.Thread(target=server.serve_forever, daemon=True).start()
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
