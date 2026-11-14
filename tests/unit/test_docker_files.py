"""Docker 이미지와 compose 구성이 지켜야 할 것 (감사 O-03, O-04). 파일을 글로 읽어 확인한다 (docker 를 띄우지 않는다).

- compose: DB 와 API 를 이 컴퓨터에만 연다, 비밀번호를 파일에 적지 않는다, DB 가 down 뒤에도 남는다.
- 이미지: 실제 어댑터가 쓰는 extra 를 설치한다, root 로 돌지 않는다, 건강 검사가 있다,
  DB 주소를 정하지 않아도 파일 DB 다 (in-memory DB + 스레드 디스패처 조합을 피한다, A-41).
- 빌드 컨텍스트: 허용한 것만 들어간다 (.demo 의 키·DB, 산출물, .git 이 이미지 빌드로 가지 않는다).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")


def _lines(text: str) -> list[str]:
    """주석을 뗀 줄."""
    return [
        line.split("#", 1)[0].rstrip()
        for line in text.splitlines()
        if line.split("#", 1)[0].strip()
    ]


def _published_ports() -> list[str]:
    return re.findall(r'^\s*-\s*"([^"]*\d+:\d+)"', COMPOSE, flags=re.MULTILINE)


def test_compose_publishes_its_ports_on_this_machine_only():
    """DB 와, 인증·요청 제한이 없는 API 를 모든 인터페이스에 열지 않는다."""
    ports = _published_ports()

    assert len(ports) == 2
    assert all(port.startswith("127.0.0.1:") for port in ports), ports


def test_compose_takes_passwords_from_the_environment():
    passwords = [line for line in _lines(COMPOSE) if "PASSWORD" in line.split(":", 1)[0]]

    assert len(passwords) == 2
    assert all("${" in line and ":?" in line for line in passwords), passwords
    # DB 주소에도 비밀번호를 적지 않는다
    (url,) = [line for line in _lines(COMPOSE) if "FRAME_DB_URL" in line]
    assert "${FRAME_DB_PASSWORD" in url and "frame:frame@" not in url


def test_compose_keeps_the_database_between_runs():
    assert re.search(r"-\s*db_data:/var/lib/mysql", COMPOSE)
    assert re.search(r"^volumes:\n(?:\s+\w+:.*\n)*\s+db_data:", COMPOSE, flags=re.MULTILINE)


def test_the_database_health_check_asks_over_tcp():
    """MySQL 은 초기화하는 동안 소켓으로만 임시 서버를 띄운다. 소켓으로 물으면 초기화가 끝나기 전에 통과한다."""
    (check,) = [line for line in _lines(COMPOSE) if "mysqladmin" in line]

    assert "--protocol=tcp" in check and "-pframe" not in check


def test_the_image_installs_what_the_real_adapters_need():
    (install,) = [line for line in _lines(DOCKERFILE) if "pip install" in line]

    assert "[llm,video]" in install  # LLM 구조화·VLM 검수기, 영상 API 의 클립 정규화(ffmpeg)
    assert "cryptography" in install  # MySQL 8.4 의 기본 인증에 pymysql 이 쓴다


def test_the_image_does_not_run_as_root_and_reports_its_health():
    lines = _lines(DOCKERFILE)
    user = [index for index, line in enumerate(lines) if line.startswith("USER ")]
    command = [index for index, line in enumerate(lines) if line.startswith("CMD ")]

    assert user and lines[user[-1]] != "USER root" and user[-1] < command[-1]
    assert any(line.startswith("HEALTHCHECK") for line in lines) and "/health" in DOCKERFILE


def test_the_image_uses_a_file_database_unless_told_otherwise():
    assert re.search(r"FRAME_DB_URL=sqlite\+pysqlite:////data/", DOCKERFILE)


def test_only_what_the_image_needs_enters_the_build_context():
    ignore = _lines((ROOT / ".dockerignore").read_text(encoding="utf-8"))

    assert ignore[0] == "*"  # 모두 빼고
    allowed = {line[1:].rstrip("/") for line in ignore if line.startswith("!")}
    assert allowed == {"pyproject.toml", "README.md", "src"}  # 필요한 것만 넣는다
