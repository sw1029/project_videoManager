"""테스트 실행 장치의 규칙 (ADR-0030): 실제 호출 스위치, 환경 격리, 루프백 밖 연결 차단, 기본 마커 필터."""

from __future__ import annotations

import importlib.metadata
import os
import socket
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support.pins import exact_pins, pin_problems
from tests.support.switches import (
    API_KEY_VARIABLES,
    drawtext_required,
    ffmpeg_required,
    is_loopback,
    korean_font_required,
    live_tests_enabled,
    missing_drawtext_message,
    missing_ffmpeg_message,
    missing_korean_font_message,
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


def test_a_korean_font_is_required_only_when_asked():
    assert korean_font_required({"FRAME_REQUIRE_KOREAN_FONT": "1"})
    assert not korean_font_required({})
    assert not korean_font_required({"FRAME_REQUIRE_FFMPEG": "1"})  # ffmpeg 의 스위치와는 따로다


def test_a_missing_korean_font_fails_the_run_only_when_required():
    """한글 글꼴을 깐 CI 잡은 화면 문구 합성 테스트를 건너뛴 채 통과로 보이지 않게, 글꼴이 없으면 실패한다."""
    required = {"FRAME_REQUIRE_KOREAN_FONT": "1"}

    assert "한글 글꼴을 찾지 못했습니다" in (missing_korean_font_message(required, None) or "")
    assert missing_korean_font_message(required, "fonts/korean.ttc") is None
    assert missing_korean_font_message({}, None) is None  # 로컬과 다른 잡에서는 지금처럼 건너뛴다


def test_an_ffmpeg_that_draws_text_is_required_only_when_asked():
    assert drawtext_required({"FRAME_REQUIRE_DRAWTEXT": "1"})
    assert not drawtext_required({})
    assert not drawtext_required(
        {"FRAME_REQUIRE_FFMPEG": "1"}
    )  # ffmpeg 가 있다고 글자를 그릴 수 있는 것은 아니다


def test_an_ffmpeg_without_drawtext_fails_the_run_only_when_required():
    """화면 문구 합성을 돌리려는 CI 잡은 drawtext 가 없는 ffmpeg 를 만나면 건너뛰지 않고 실패한다 (감사 A-43)."""
    required = {"FRAME_REQUIRE_DRAWTEXT": "1"}

    problem = "some-ffmpeg 에 drawtext 필터가 없습니다 (목록에서 읽은 필터 412개)"

    message = missing_drawtext_message(required, problem=problem) or ""
    assert "FRAME_REQUIRE_DRAWTEXT=1" in message and problem in message  # 어느 ffmpeg 인지, 왜인지
    assert missing_drawtext_message(required, problem=None) is None
    assert missing_drawtext_message({}, problem=problem) is None  # 다른 잡과 로컬은 건너뛴다


def test_why_text_cannot_be_drawn_names_the_ffmpeg_and_the_cause(monkeypatch):
    """'필터가 없다' 와 '목록을 읽지 못했다' 는 고치는 곳이 다르다 (ffmpeg 를 바꾼다 / 읽는 법을 본다).

    CI 에서 drawtext 가 든 ffmpeg 를 깔았는데도 '필터가 없습니다' 로 멈췄을 때, 문구에 어느 ffmpeg 를 찾았는지와
    목록을 얼마나 읽었는지가 없어 까닭을 로그만으로 알 수 없었다 (감사 A-43 의 둘째 실패).
    """
    from frame.application.errors import AdapterError
    from tests.support import video

    assert "찾지 못했습니다" in (video.drawtext_problem(None) or "")

    monkeypatch.setattr(video, "available_filters", lambda ffmpeg: frozenset({"drawbox", "scale"}))
    missing = video.drawtext_problem("some-ffmpeg") or ""
    assert "some-ffmpeg" in missing and "drawtext 필터가 없습니다" in missing and "2개" in missing
    assert video.can_draw_text("some-ffmpeg") is False

    def unreadable(ffmpeg):
        raise AdapterError("목록의 모양이 달라졌을 수 있습니다")

    monkeypatch.setattr(video, "available_filters", unreadable)
    unread = video.drawtext_problem("some-ffmpeg") or ""
    assert "some-ffmpeg" in unread and "읽지 못했습니다" in unread and "달라졌을" in unread

    monkeypatch.setattr(video, "available_filters", lambda ffmpeg: frozenset({"drawtext"}))
    assert video.drawtext_problem("some-ffmpeg") is None and video.can_draw_text("some-ffmpeg")


def test_pytest_leaves_paid_tests_out_by_default():
    options = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = options["tool"]["pytest"]["ini_options"]["addopts"]

    assert "-m 'not slow and not gpu'" in addopts  # 명령줄의 -m 이 뒤에 와서 이긴다
    assert "--strict-markers" in addopts and "--strict-config" in addopts


def test_exact_pins_are_the_requirements_written_with_two_equals_signs():
    requirements = [
        "fastapi==0.133.0",
        "starlette == 1.3.1",
        "sqlalchemy>=2.0,<3",  # 범위는 고정이 아니다
        "tomli==2.0.1; python_version < '3.11'",  # 조건이 붙은 것도 뺀다
    ]

    assert exact_pins(requirements) == {"fastapi": "0.133.0", "starlette": "1.3.1"}


def test_a_pin_that_differs_from_what_is_installed_is_reported():
    installed = {"fastapi": "0.115.13", "ruff": "0.17.4"}.get

    problems = pin_problems(
        required={"fastapi": "0.133.0", "uvicorn": "0.34.2"},
        optional={"ruff": "0.17.4", "torch": "2.5.1"},  # 선택 설치는 없어도 된다
        installed=installed,
    )

    assert problems == [
        "fastapi: 설치된 판 0.115.13, 고정 0.133.0",
        "uvicorn: 설치되지 않음 (고정 0.34.2)",
    ]


def test_the_installed_packages_are_the_pinned_ones():
    """정확히 고정한 판(pyproject 의 ==)과 이 환경에 설치된 판이 같다 (ADR-0033).

    다르면 로컬의 결과가 CI 와 달라진다 (OpenAPI 명세 · 포맷 · 타입 검사). 고정을 올린 뒤에는 설치를 다시 한다.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    extras = [line for lines in project["optional-dependencies"].values() for line in lines]

    def installed(name: str) -> str | None:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return None

    problems = pin_problems(exact_pins(project["dependencies"]), exact_pins(extras), installed)

    assert problems == [], (
        "설치된 판이 pyproject.toml 의 고정과 다르다. pip install -e '.[dev,llm,video]' 로 다시 설치한다"
    )


def test_every_live_test_module_is_marked_slow():
    """tests/live 의 테스트는 slow 로 표시돼야 기본 실행에서 빠지고, 환경 격리의 예외가 된다."""
    modules = sorted((ROOT / "tests" / "live").glob("test_*.py"))

    assert modules
    unmarked = [m.name for m in modules if "pytest.mark.slow" not in m.read_text(encoding="utf-8")]
    assert unmarked == []
    assert (ROOT / "tests" / "live" / "conftest.py").is_file()
