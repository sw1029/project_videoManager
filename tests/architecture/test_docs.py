"""문서와 코드의 일치 (ADR-0030): ADR 색인과 모듈 지도가 실제 파일과 같아야 한다.

ADR 을 더하면 docs/adr/README.md 에, 패키지를 더하거나 옮기면 docs/MODULE_MAP.md 에 한 줄을 고친다.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.architecture.imports import ROOT, packages

ADR_FOLDER = ROOT / "docs" / "adr"
ADR_INDEX = ADR_FOLDER / "README.md"
MODULE_MAP = ROOT / "docs" / "MODULE_MAP.md"
ADR_FILE_NAME = re.compile(r"(\d{4})-[a-z0-9-]+\.md")
INDEX_ROW = re.compile(r"^\| \[(\d{4})\]\((\d{4}-[a-z0-9-]+\.md)\) \|", re.MULTILINE)
MAP_ROW = re.compile(r"^\| `(src/frame/[a-z_/]*)` \|", re.MULTILINE)


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


def test_the_module_map_lists_every_package():
    listed = set(MAP_ROW.findall(MODULE_MAP.read_text(encoding="utf-8")))
    actual = {"src/" + name.replace(".", "/") + "/" for name in packages()}

    assert actual - listed == set(), "docs/MODULE_MAP.md 의 패키지 목록에 없는 패키지"
    assert listed - actual == set(), "패키지 목록에만 있고 실제로는 없는 패키지"
