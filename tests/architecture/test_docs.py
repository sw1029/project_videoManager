"""문서와 코드의 일치 (ADR-0030): ADR 색인과 모듈 지도가 실제 파일과 같아야 한다.

ADR 을 더하면 docs/adr/README.md 에, 패키지를 더하거나 옮기면 docs/MODULE_MAP.md 에 한 줄을 고친다.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterator, Mapping
from pathlib import Path

from frame.config import Settings
from frame.config.theme_settings import ThemeSettings
from tests.architecture.imports import ROOT, packages
from tests.support.pins import exact_pins

ADR_FOLDER = ROOT / "docs" / "adr"
ADR_INDEX = ADR_FOLDER / "README.md"
MODULE_MAP = ROOT / "docs" / "MODULE_MAP.md"
ARCHITECTURE = ROOT / "docs" / "ARCHITECTURE.md"
# 설계 문서의 스택 줄에 판을 적는 패키지: pyproject 의 이름 -> 문서에 적는 이름 (ADR-0033)
DOCUMENTED_STACK = {"fastapi": "FastAPI", "starlette": "Starlette"}
ADR_FILE_NAME = re.compile(r"(\d{4})-[a-z0-9-]+\.md")
INDEX_ROW = re.compile(r"^\| \[(\d{4})\]\((\d{4}-[a-z0-9-]+\.md)\) \|", re.MULTILINE)
MAP_ROW = re.compile(r"^\| `(src/frame/[a-z_/]*)` \|", re.MULTILINE)
# 실제 호출 테스트를 돌리는 명령: pytest 뒤에 (같은 코드 조각 안에서) tests/live 나 -m slow 가 온다
LIVE_TEST_COMMAND = re.compile(r"\bpytest [^`\n]*(?:tests/live|-m slow\b)")
LIVE_TEST_SWITCH = "FRAME_LIVE_TESTS=1"
ENV_EXAMPLE = ROOT / ".env.example"
# 예시 파일이 적은 설정: 줄 머리의 FRAME_이름= (주석으로 꺼 둔 줄도 센다)
EXAMPLE_VARIABLE = re.compile(r"^#?\s*(FRAME_[A-Z0-9_]+)=", re.MULTILINE)


def _adr_files() -> list[Path]:
    return sorted(ADR_FOLDER.glob("[0-9]*.md"))


def test_adr_files_are_named_and_titled_by_their_number():
    assert _adr_files()
    for path in _adr_files():
        match = ADR_FILE_NAME.fullmatch(path.name)
        assert match, f"ADR 파일 이름은 NNNN-짧은-영문-제목.md 다: {path.name}"
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith(f"# ADR-{match.group(1)} "), (
            f"{path.name} 의 첫 줄: {first_line}"
        )


def test_adr_numbers_are_not_reused():
    numbers = [path.name[:4] for path in _adr_files()]

    assert sorted({n for n in numbers if numbers.count(n) > 1}) == []


def test_the_adr_index_lists_every_adr():
    rows = INDEX_ROW.findall(ADR_INDEX.read_text(encoding="utf-8"))
    listed = {name for _, name in rows}
    files = {path.name for path in _adr_files()}

    assert files - listed == set(), "docs/adr/README.md 의 표에 없는 ADR"
    assert listed - files == set(), "표에만 있고 파일이 없는 ADR"
    assert all(name.startswith(number + "-") for number, name in rows), "번호와 링크가 다른 줄"


def test_the_documented_stack_names_the_pinned_versions():
    """설계 문서가 적은 판은 pyproject.toml 의 고정과 같다. 고정을 올리면 문서의 스택 줄도 고친다."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pins = exact_pins(project["dependencies"])
    text = ARCHITECTURE.read_text(encoding="utf-8")

    missing = [
        f"{shown} {pins[name]}"
        for name, shown in DOCUMENTED_STACK.items()
        if f"{shown} {pins[name]}" not in text
    ]
    assert missing == [], "docs/ARCHITECTURE.md 의 스택 줄에 없는 판"


def test_the_module_map_lists_every_package():
    listed = set(MAP_ROW.findall(MODULE_MAP.read_text(encoding="utf-8")))
    actual = {"src/" + name.replace(".", "/") + "/" for name in packages()}

    assert actual - listed == set(), "docs/MODULE_MAP.md 의 패키지 목록에 없는 패키지"
    assert listed - actual == set(), "패키지 목록에만 있고 실제로는 없는 패키지"


def _where_commands_are_written() -> list[Path]:
    """실제 호출 테스트의 실행 명령을 적는 곳: README, docs 바로 아래의 문서, 그 테스트들과 src 의 머리말.

    ADR 과 감사 보고서는 보지 않는다 (그때의 기록이다).
    """
    return [
        ROOT / "README.md",
        *sorted((ROOT / "docs").glob("*.md")),
        *sorted((ROOT / "tests" / "live").glob("*.py")),
        *sorted((ROOT / "src").rglob("*.py")),
    ]


def test_documented_live_test_commands_carry_the_switch():
    """적어 둔 실제 호출 테스트의 실행 명령에는 FRAME_LIVE_TESTS=1 이 있다 (감사 P-20).

    tests/live 의 테스트는 그 스위치가 없으면 모두 건너뛴다 (tests/live/conftest.py). 스위치가 빠진 명령을 그대로 치면
    '모두 건너뜀' 이 통과로 보인다. 브랜치마다 따로 적은 명령이 dev 에서 그 스위치와 만나며 어긋났었다.
    """
    missing = []
    for path in _where_commands_are_written():
        # 줄 끝의 역슬래시로 이은 명령은 한 줄로 본다
        text = re.sub(r"\\\n\s*", " ", path.read_text(encoding="utf-8"))
        missing += [
            f"{path.relative_to(ROOT).as_posix()}: {line.strip()[:140]}"
            for line in text.splitlines()
            if LIVE_TEST_COMMAND.search(line) and LIVE_TEST_SWITCH not in line
        ]

    assert missing == [], f"{LIVE_TEST_SWITCH} 이 없는 명령:\n  " + "\n  ".join(missing)


class _AskedNames(Mapping[str, str]):
    """아무 값도 없는 환경. 읽으려 한 이름을 적어 둔다."""

    def __init__(self) -> None:
        self.asked: set[str] = set()

    def __getitem__(self, name: str) -> str:
        self.asked.add(name)
        raise KeyError(name)

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0


def _variables_read_at_startup() -> set[str]:
    """서버가 기동할 때 읽는 환경 변수의 이름 (Settings 와 ThemeSettings 의 from_env 가 묻는 것)."""
    names: set[str] = set()
    for read in (Settings.from_env, ThemeSettings.from_env):
        env = _AskedNames()
        read(env)
        names |= env.asked
    return names


def test_the_example_environment_lists_every_setting_the_server_reads():
    """.env.example 은 서버가 읽는 FRAME_* 설정을 모두 적고, 읽지 않는 이름은 적지 않는다 (감사 P-20 · BTS-D68).

    설정을 더하는 브랜치가 예시를 빠뜨리면 그 설정은 안내 없이 남는다. 테마 · 시리즈의 설정 23개가 그랬다.
    """
    read = _variables_read_at_startup()
    written = set(EXAMPLE_VARIABLE.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))

    assert len(read) > 70  # 두 설정의 이름을 제대로 받았다
    assert sorted(read - written) == [], ".env.example 에 없는 설정"
    assert sorted(written - read) == [], "서버가 읽지 않는 이름"
