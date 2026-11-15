"""Docker 이미지와 compose 구성이 지켜야 할 것 (감사 O-03, O-04). 파일을 글로 읽어 확인한다 (docker 를 띄우지 않는다).

- compose: DB 와 API 를 이 컴퓨터에만 연다, 비밀번호를 파일에 적지 않는다, DB 가 down 뒤에도 남는다.
- 이미지: 실제 어댑터가 쓰는 extra 를 설치한다, root 로 돌지 않는다, 건강 검사가 있다,
  DB 주소를 정하지 않아도 파일 DB 다 (in-memory DB + 스레드 디스패처 조합을 피한다, A-41).
  영상 처리는 배포판의 ffmpeg 로 하고 한글 글꼴을 갖춘다 (ADR-0034, A-43).
- CI 의 docker 잡: 만든 이미지 안에서 실제 영상 경로(전체 테스트)를 돌린다 - 건너뛰지 않게.
- 빌드 컨텍스트: 허용한 것만 들어간다 (.demo 의 키·DB, 산출물, .git 이 이미지 빌드로 가지 않는다).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


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

    assert '".[llm]"' in install  # LLM 구조화·VLM 검수기
    assert "cryptography" in install  # MySQL 8.4 의 기본 인증에 pymysql 이 쓴다
    # ffmpeg 는 배포판의 것 하나만 둔다. PATH 의 ffmpeg 가 먼저 쓰이므로 번들(video extra)은 넣어도 쓰이지 않는다
    assert "video" not in install


def _apt_install() -> str:
    """apt-get install 이 든 RUN 명령 하나 (줄 이음을 편 것)."""
    joined = re.sub(r"\\\n\s*", " ", "\n".join(_lines(DOCKERFILE)))
    (command,) = [line for line in joined.splitlines() if "apt-get install" in line]
    return command


def test_the_image_brings_the_distributions_ffmpeg_and_a_korean_font():
    """video extra 가 Linux 에 가져오는 ffmpeg 에는 화면 문구를 그리는 drawtext 필터가 없다 (A-43).

    이미지는 배포판의 ffmpeg 와 한글 글꼴을 넣는다 (ADR-0034). 추천 꾸러미는 넣지 않고 색인은 지운다.
    """
    command = _apt_install()
    packages = set(command.split())

    assert {"ffmpeg", "fonts-nanum"} <= packages
    assert "--no-install-recommends" in packages
    assert "apt-get update" in command and "rm -rf" in command and "apt/lists" in command


def test_the_base_image_names_its_debian_release():
    """이미지의 ffmpeg 는 배포판의 것이다. 배포판을 이름으로 고정해야 ffmpeg 의 판이 모르는 사이에 바뀌지 않는다."""
    (base,) = [line for line in _lines(DOCKERFILE) if line.startswith("FROM ")]

    assert re.fullmatch(r"FROM python:3\.11-slim-[a-z]+", base), base


def _docker_job() -> str:
    """ci.yml 의 docker 잡 (다음 잡의 머리 전까지)."""
    match = re.search(
        r"^  docker:\n(.*?)(?=^  [a-z][a-z-]*:\n)", WORKFLOW, flags=re.MULTILINE | re.DOTALL
    )
    assert match is not None
    return match.group(1)


def test_ci_shows_which_ffmpeg_and_font_the_image_has():
    job = _docker_job()

    assert "docker image ls" in job and "docker history" in job  # 이미지의 크기, 층마다의 크기
    assert "ffmpeg -hide_banner -version" in job  # 배포판 ffmpeg 의 판
    assert 'grep " drawtext "' in job  # 글자를 그리는 필터가 없으면 그 단계에서 실패한다


def test_ci_runs_the_real_media_paths_inside_the_image():
    """이미지 안에서는 배포판의 ffmpeg 가 모든 영상 경로에 쓰인다 (정규화 · 조립 · 프레임 뽑기 · 화면 문구).

    그 판으로 되는지를 만든 이미지 안에서 전체 테스트로 확인한다. ffmpeg · drawtext · 한글 글꼴이 없으면 건너뛰지 않고
    실패하게 스위치를 켜고, 이미지가 정한 사용자(서버가 도는 frame)로 돌린다 - root 로 바꾸지 않는다.
    """
    job = _docker_job()

    assert "python -m pytest" in job
    for switch in (
        "FRAME_REQUIRE_FFMPEG=1",
        "FRAME_REQUIRE_KOREAN_FONT=1",
        "FRAME_REQUIRE_DRAWTEXT=1",
    ):
        assert switch in job, switch
    assert "--user" not in job


def test_ci_assembles_the_server_with_text_overlays_in_the_image_as_shipped():
    """개발 도구를 더하지 않은 이미지 그대로, 화면 문구 합성을 켠 서버가 조립되는지 본다.

    그 어댑터는 만들어질 때 글꼴과 drawtext 필터를 확인한다 (없으면 ConfigurationError 로 기동이 멈춘다).
    """
    job = _docker_job()

    assert "FRAME_OVERLAY_RENDERER=ffmpeg" in job and "create_default_app" in job


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
