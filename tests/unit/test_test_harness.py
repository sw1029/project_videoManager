"""테스트 실행 장치의 규칙 (ADR-0030): 실제 호출 스위치, 환경 격리, 루프백 밖 연결 차단, 기본 마커 필터."""

from __future__ import annotations

import os
import socket
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support.switches import (
    API_KEY_VARIABLES,
    ffmpeg_required,
    is_loopback,
    live_tests_enabled,
    missing_ffmpeg_message,
    shell_leak_names,
)

ROOT = Path(__file__).resolve().parents[2]
PROBE = "FRAME_HARNESS_PROBE"


@pytest.fixture(scope="module", autouse=True)
def _shell_with_settings_and_keys() -> Iterator[None]:
    """개발자 셸에 서버 설정과 키가 남아 있는 상황을 흉내 낸다 (이미 있는 값은 건드리지 않는다)."""
    added = [name for name in (PROBE, *API_KEY_VARIABLES) if name not in os.environ]
    for name in added:
        os.environ[name] = "left-in-the-shell"
    yield
    for name in added:
        os.environ.pop(name, None)


def test_shell_settings_and_keys_do_not_reach_a_test():
    assert PROBE not in os.environ
    assert [name for name in API_KEY_VARIABLES if name in os.environ] == []
    assert shell_leak_names(os.environ) == []


def test_only_server_settings_and_api_keys_are_removed():
    env = {"FRAME_GENERATOR": "external_api", "OPENAI_API_KEY": "k", "PATH": "/bin", "HOME": "/h"}

    assert shell_leak_names(env) == ["FRAME_GENERATOR", "OPENAI_API_KEY"]


def test_a_test_cannot_connect_outside_this_machine():
    with socket.socket() as sock, pytest.raises(AssertionError, match="루프백이 아닌 주소"):
        sock.connect(("203.0.113.7", 9))  # 문서용 주소(TEST-NET-3). 막히는 것이 연결보다 먼저다


def test_loopback_connections_still_work():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket() as client:
            client.connect(server.getsockname())


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("localhost", True),
        ("openrouter.ai", False),
        (None, False),
    ],
)
def test_loopback_hosts(host, expected):
    assert is_loopback(host) is expected


def test_live_tests_need_their_own_switch():
    """서버를 띄우던 셸의 설정만으로는 유료 테스트가 켜지지 않는다."""
    assert live_tests_enabled({"FRAME_LIVE_TESTS": "1"})
    assert not live_tests_enabled({"FRAME_GENERATOR": "external_api", "FRAME_STRUCTURER": "llm"})
    assert not live_tests_enabled({"FRAME_LIVE_TESTS": "0"})
    assert not live_tests_enabled({})


def test_ffmpeg_is_required_only_when_asked():
    assert ffmpeg_required({"FRAME_REQUIRE_FFMPEG": "1"})
    assert not ffmpeg_required({})


def test_a_missing_ffmpeg_fails_the_run_only_when_required():
    """CI 는 ffmpeg 가 없으면 실제 영상 테스트를 조용히 건너뛰지 않고 실패한다."""
    required = {"FRAME_REQUIRE_FFMPEG": "1"}

    assert "ffmpeg 를 찾지 못했습니다" in (missing_ffmpeg_message(required, None) or "")
    assert missing_ffmpeg_message(required, "bin/ffmpeg") is None
    assert missing_ffmpeg_message({}, None) is None  # 로컬에서는 지금처럼 건너뛴다


def test_pytest_leaves_paid_tests_out_by_default():
    options = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = options["tool"]["pytest"]["ini_options"]["addopts"]

    assert "-m 'not slow and not gpu'" in addopts  # 명령줄의 -m 이 뒤에 와서 이긴다
    assert "--strict-markers" in addopts and "--strict-config" in addopts


def test_every_live_test_module_is_marked_slow():
    """tests/live 의 테스트는 slow 로 표시돼야 기본 실행에서 빠지고, 환경 격리의 예외가 된다."""
    modules = sorted((ROOT / "tests" / "live").glob("test_*.py"))

    assert modules
    unmarked = [m.name for m in modules if "pytest.mark.slow" not in m.read_text(encoding="utf-8")]
    assert unmarked == []
    assert (ROOT / "tests" / "live" / "conftest.py").is_file()
