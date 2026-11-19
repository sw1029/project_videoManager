from __future__ import annotations

import importlib.util
import os
import socket
from typing import Any

import pytest

from frame.config import Container
from tests.support.factories import cafe_ir, create_draft, dispatcher_of, make_container
from tests.support.switches import (
    is_loopback,
    missing_drawtext_message,
    missing_ffmpeg_message,
    missing_korean_font_message,
    missing_opencv_message,
    shell_leak_names,
)
from tests.support.video import drawtext_problem, find_ffmpeg, find_korean_font


def _opencv_available() -> bool:
    return importlib.util.find_spec("cv2") is not None


def pytest_report_header(config: pytest.Config) -> list[str]:
    """실제 영상 테스트가 도는지 한눈에 보이게 한다 (ffmpeg · 한글 글꼴이 없으면 그 테스트들은 건너뛴다)."""
    ffmpeg = find_ffmpeg()
    problem = drawtext_problem(ffmpeg) if ffmpeg is not None else None
    drawing = (
        f" (글자를 그릴 수 없음: {problem} — 화면 문구 합성 테스트는 건너뜀)" if problem else ""
    )
    return [
        f"ffmpeg: {ffmpeg or '없음 — 실제 영상 테스트는 건너뜀'}{drawing}",
        f"한글 글꼴: {find_korean_font() or '없음 — 화면 문구 합성 테스트는 건너뜀'}",
        f"OpenCV: {'있음' if _opencv_available() else '없음 — 추적기(opencv) 테스트는 건너뜀'}",
    ]


def pytest_sessionstart(session: pytest.Session) -> None:
    """CI 는 FRAME_REQUIRE_FFMPEG=1 로 돈다: ffmpeg 가 없으면 조용히 건너뛰지 않고 실패한다 (ADR-0030).

    화면 문구 합성을 도는 잡은 FRAME_REQUIRE_KOREAN_FONT=1 · FRAME_REQUIRE_DRAWTEXT=1 로 돈다:
    한글 글꼴이나 글자를 그리는 ffmpeg 를 찾지 못하면 같은 식으로 실패한다.
    test 잡은 FRAME_REQUIRE_OPENCV=1 로도 돈다: tracking extra 가 없으면 추적기 테스트가 조용히 빠지지 않게.
    """
    ffmpeg = find_ffmpeg()
    messages = (
        missing_ffmpeg_message(os.environ, ffmpeg),
        missing_korean_font_message(os.environ, find_korean_font()),
        missing_drawtext_message(os.environ, problem=drawtext_problem(ffmpeg)),
        missing_opencv_message(os.environ, available=_opencv_available()),
    )
    for message in messages:
        if message is not None:
            raise pytest.UsageError(message)


@pytest.fixture(autouse=True)
def _isolated_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """개발자 셸의 서버 설정(FRAME_*)과 API 키가 테스트에 새지 않게 한다. 실제 호출 테스트(slow)는 그대로 둔다."""
    if request.node.get_closest_marker("slow") is not None:
        return
    for name in shell_leak_names(os.environ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _loopback_only(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """실제 호출이 아닌 테스트는 이 기기 밖으로 연결하지 못한다.

    주소 설정 하나가 빠진 실수가 유료 호출이 되지 않게 한다 (루프백 가짜 서버로 가는 연결은 그대로 된다).
    """
    if request.node.get_closest_marker("slow") is not None:
        return
    connect = socket.socket.connect

    def guarded(self: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(address, tuple) and not is_loopback(address[0]):
            raise AssertionError(
                f"테스트가 루프백이 아닌 주소로 연결하려 했습니다: {address[0]!r} "
                "(실제 호출 테스트는 tests/live 에 두고 slow 로 표시한다)"
            )
        return connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def ir():
    return cafe_ir()


@pytest.fixture
def container() -> Container:
    return make_container()


@pytest.fixture
def dispatcher(container):
    return dispatcher_of(container)


@pytest.fixture
def draft(container):
    """구조화가 끝난 DRAFT 버전 (CreateProjectResult)."""
    return create_draft(container)
