"""컴퓨터에 묶인 절대 경로 (ADR-0032): 저장소에 커밋되는 글에 특정 컴퓨터의 경로를 적지 않는다.

찾는 것은 드라이브 문자 경로(`C:` 뒤에 `\\` 나 `/`)와, 홈 · 설치 · 임시 폴더에서 시작하는 POSIX 경로다.
그런 경로는 그것을 적은 컴퓨터에서만 맞다 — 키 파일의 위치, 글꼴 · 도구가 깔린 자리, 사용자 이름이 든 폴더.

대신 쓰는 것
    설정(환경 변수), 저장소 기준 상대 경로, 테스트의 tmp_path, 운영체제가 알려 주는 값(os.environ · os.confstr · 탐색),
    문서의 자리표시 (`path/to/openai.key`).

꼭 있어야 하는 경로는 ALLOWED 에 (파일 · 이유 · 그 줄에 든 글)로 적는다. 허용하는 것은 네 가지뿐이다:
경로를 거절 · 변환 · 가리는지 시험하는 입력, 컨테이너 안의 경로, 규격이 정한 위치, 사람에게 보이는 안내 · 설명의 예시.
사용자 이름이 들어가는 예시에는 실제 이름이 아니라 someone 같은 중립 이름을 쓴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

import pytest

from tests.architecture.imports import ROOT

# Windows 드라이브 경로: 드라이브 문자 뒤에 \ 또는 / (주소의 '://' 는 아니다)
DRIVE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:(?:\\{1,2}|/(?!/))(?=[A-Za-z0-9_.{<%$~-])")
# POSIX 의 컴퓨터에 묶인 뿌리: 홈 · 설치 위치 · 임시 폴더. 주소의 경로(/api/...)나 상대 경로의 조각은 아니다
POSIX = re.compile(
    r"(?<![A-Za-z0-9_.~$}{/\\-])/(?:home|Users|root|usr|opt|etc|var|tmp|mnt|media|srv|private|"
    r"Library|Applications|Volumes|System)/[A-Za-z0-9_.~-]"
)

# 훑는 곳: 저장소에 커밋되는 글. 화면(frontend/)은 이 검사의 범위 밖이다
FOLDERS = (".github", "docker", "docs", "scripts", "src", "tests")
TOP_LEVEL_SUFFIXES = (".md", ".toml", ".yml", ".yaml", ".example", ".cfg", ".ini", ".txt")
TOP_LEVEL_NAMES = (".dockerignore", ".gitattributes", ".gitignore")
SKIPPED_FOLDERS = ("__pycache__", "node_modules", ".pytest_cache", ".ruff_cache", ".mypy_cache")
BINARY_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".mp4",
    ".wav",
    ".pdf",
    ".woff",
    ".woff2",
)
THIS_FILE = "tests/architecture/test_machine_paths.py"  # 아래 표가 그 경로들을 적고 있다


@dataclass(frozen=True)
class Allowed:
    file: str
    reason: str
    lines: tuple[str, ...]  # 그 파일의 허용하는 줄마다, 그 줄에 든 글 한 조각


REJECTED = "절대 경로를 거절하는지 시험하는 입력"
MASKED = "오류 문구 · 기록 · 로그에서 경로가 어떻게 다뤄지는지 시험하는 입력"
CONVERTED = "경로를 다른 모양으로 바꾸는 규칙을 시험하는 입력"
GUIDANCE = "사람에게 보이는 안내의 예시"
EXPLAINED = "규칙을 설명하는 글의 예시 (줄임표나 중립 이름으로 적었다)"

ALLOWED: tuple[Allowed, ...] = (
    # ---- 컨테이너 안의 경로 · 규격이 정한 위치 ----
    Allowed(
        "docker/docker-compose.yml",
        "컨테이너 안의 경로 (MySQL 이미지가 정한 자료 폴더)",
        ("db_data:/var/lib/mysql",),
    ),
    Allowed(
        "tests/unit/test_docker_files.py",
        "위 compose 줄이 그대로 있는지 확인하는 테스트",
        ("db_data:/var/lib/mysql",),
    ),
    Allowed(
        "src/frame/adapters/series/overlay/ffmpeg_renderer.py",
        "규격이 정한 위치: XDG Base Directory 의 기본값과 macOS 의 글꼴 폴더 (글꼴은 그 폴더들에서 이름으로 찾는다)",
        (
            '_XDG_DATA_DIRS = "/usr/local/share:/usr/share"',
            "/Library/Fonts, /System/Library/Fonts",
            'Path("/Library/Fonts"), Path("/System/Library/Fonts")',
        ),
    ),
    # ---- 사람에게 보이는 안내 · 설명 ----
    Allowed("docs/DEMO.md", GUIDANCE, ("`C:\\frame` 처럼 짧은 경로", "Windows 폴더(`/mnt/c/...`)")),
    Allowed(
        "src/frame/tools/bundle.py",
        GUIDANCE,
        ("(예: C:\\Users\\이름\\Downloads\\폴더\\)", "C:\\\\frame 처럼 짧은 경로에 푸세요"),
    ),
    Allowed(
        "tests/unit/test_bundle_tool.py",
        "위 안내 문구가 번들의 README 에 들어가는지 확인하는 테스트",
        ('"C:\\\\frame" in windows',),
    ),
    Allowed(
        "docs/audit/2026-11-backend-audit.md",
        EXPLAINED,
        ("`C:\\Users\\…\\Temp\\frame-frames-…`",),
    ),
    Allowed("src/frame/evaluation/edit_report.py", EXPLAINED, ("(C:\\Users\\Kim Lee\\…)",)),
    Allowed(
        "src/frame/evaluation/structuring/_public.py", EXPLAINED, ("(C:\\Users\\Al 뒤의 ice)",)
    ),
    Allowed("src/frame/tools/demo.py", EXPLAINED, ("(Windows: ///C:/..., POSIX: ////home/...)",)),
    # ---- 경로를 거절 · 변환 · 가리는지 시험하는 입력 ----
    Allowed("tests/contract/test_artifact_store_contract.py", REJECTED, ('"C:/clips/intro.mp4"',)),
    Allowed(
        "tests/unit/application/test_reference_image_upload.py",
        REJECTED,
        ('"C:/Users/secret.png"',),
    ),
    Allowed(
        "tests/unit/generator/test_openrouter_generator.py",
        REJECTED,
        ('"C:/Users/someone/secret.png", "file:///etc/passwd"',),
    ),
    Allowed(
        "tests/evaluation/test_structuring_gold.py",
        MASKED,
        (
            "C:\\Users\\someone\\keys\\openai.txt",
            "D:/secrets/key.txt",
            "/home/someone/.config/frame/key",
            "file:///C:/Users/someone/key.txt",
            "C:\\Users\\Jane Doe\\keys\\k.txt",
            'lambda: (r"C:\\Users\\Jane Doe",)',
            "사용자 폴더가 C:\\\\Users\\\\Al 일 때",
            'lambda: (r"C:\\Users\\Al",)',
            "C:\\Users\\Alice\\key.txt",
        ),
    ),
    Allowed("tests/unit/application/test_check_suite.py", MASKED, ("boom at /srv/frame/secret",)),
    Allowed(
        "tests/unit/application/test_extract_theme.py",
        MASKED,
        ("C:\\\\Users\\\\someone\\\\clip.wav",),
    ),
    Allowed("tests/unit/test_checker_unit_guard.py", MASKED, ('KeyError("C:/secret/path")',)),
    Allowed(
        "tests/unit/test_edit_report.py",
        MASKED,
        (
            'WINDOWS = "C:\\\\Users\\\\kim',
            'POSIX = "/home/kim/.cache/frame/parent.mp4"',
            '"C:\\\\Users\\\\Kim Lee\\\\AppData',
        ),
    ),
    Allowed(
        "tests/unit/test_demo_tool.py",
        CONVERTED,
        (
            'PureWindowsPath(r"C:\\Users\\me\\FRAME\\.demo\\frame.db")',
            'PurePosixPath("/home/me/FRAME/.demo/frame.db")',
            '"sqlite+pysqlite:///C:/Users/me/FRAME/.demo/frame.db"',
            "(예: C:\\Users\\홍길동\\...)",
        ),
    ),
)


@dataclass(frozen=True)
class Found:
    file: str
    number: int
    text: str

    def __str__(self) -> str:
        return f"{self.file}:{self.number}: {self.text.strip()[:160]}"


def _text_files() -> list[str]:
    names = [
        path.relative_to(ROOT).as_posix()
        for folder in FOLDERS
        for path in sorted((ROOT / folder).rglob("*"))
        if path.is_file()
        and path.suffix.lower() not in BINARY_SUFFIXES
        and not any(part in SKIPPED_FOLDERS for part in path.parts)
    ]
    names += [
        path.name
        for path in sorted(ROOT.iterdir())
        if path.is_file() and (path.suffix in TOP_LEVEL_SUFFIXES or path.name in TOP_LEVEL_NAMES)
    ]
    return [name for name in names if name != THIS_FILE]


def _is_machine_path(text: str) -> bool:
    return bool(DRIVE.search(text) or POSIX.search(text))


@cache
def machine_paths() -> tuple[Found, ...]:
    found: list[Found] = []
    for name in _text_files():
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # 글 파일이 아니다
        for number, line in enumerate(text.splitlines(), start=1):
            if number == 1 and line.startswith("#!"):
                continue  # 스크립트의 첫 줄 (#!/usr/bin/env sh)
            if _is_machine_path(line):
                found.append(Found(name, number, line))
    return tuple(found)


def _allowed(item: Found) -> bool:
    return any(
        entry.file == item.file and any(piece in item.text for piece in entry.lines)
        for entry in ALLOWED
    )


def test_no_machine_bound_paths_are_committed():
    wrong = [str(item) for item in machine_paths() if not _allowed(item)]

    if wrong:
        pytest.fail(
            "컴퓨터에 묶인 절대 경로가 있다. 설정 · 상대 경로 · tmp_path · 자리표시로 바꾼다 "
            "(꼭 있어야 하면 이 파일의 ALLOWED 에 이유와 함께 적는다):\n  " + "\n  ".join(wrong),
            pytrace=False,
        )


def test_every_allowed_path_is_still_there():
    """허용 목록은 줄이기만 한다: 그 줄이 없어졌으면 목록에서도 지운다."""
    lines_of: dict[str, list[str]] = {}
    for item in machine_paths():
        lines_of.setdefault(item.file, []).append(item.text)
    stale = [
        f"{entry.file}: {piece}"
        for entry in ALLOWED
        for piece in entry.lines
        if not any(piece in line for line in lines_of.get(entry.file, []))
    ]

    if stale:
        pytest.fail(
            "더는 걸리지 않는 허용 항목이 있다 (ALLOWED 에서 지운다):\n  " + "\n  ".join(stale),
            pytrace=False,
        )


def test_every_allowed_path_says_why():
    assert all(entry.reason.strip() and entry.lines for entry in ALLOWED)


def test_the_patterns_find_machine_paths_and_leave_other_text_alone():
    """정규식이 무엇을 잡고 무엇을 지나치는지 (검사 자체의 시험)."""
    for text in (
        r"C:\Users\someone\key.txt",
        "C:\\\\Users\\\\someone",  # 소스에 적힌 모양 (역슬래시 둘)
        "FRAME_LLM_KEY_FILE=F:/key.txt",
        "sqlite+pysqlite:///C:/data/frame.db",
        "/home/someone/.config/frame",
        "font = /usr/share/fonts/nanum.ttf",
        "/opt/tools/ffmpeg -version",
    ):
        assert _is_machine_path(text), text
    for text in (
        "https://openrouter.ai/api/v1/videos",
        "GET /api/versions/{version_id}",
        "path/to/openai.key",
        "./.demo/openai.key",
        "src/frame/tools/demo.py",
        "refs/uploads/cup.png",
        "비율은 9:16 이고 시각은 12:30/13:00 이다",
        "~/Library/Fonts",
        "$HOME/usr/bin",
    ):
        assert not _is_machine_path(text), text
