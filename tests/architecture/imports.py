"""src/frame 의 모듈과 import 를 AST 로 모은다 (함수 안 import 와 TYPE_CHECKING 블록도 포함한다)."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "frame"
PACKAGE = "frame"
EXTERNAL = "(external)"
TOP = "(root)"


@dataclass(frozen=True)
class Import:
    importer: str  # 가져오는 모듈
    imported: str  # 가져온 모듈. 상대 import 는 절대 이름으로 풀고, `from a import b` 의 b 가 모듈이면 a.b 다
    path: str  # 저장소 기준 경로 (보고용)
    line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  {self.importer} -> {self.imported}"


def module_name(path: Path) -> str:
    parts = list(path.relative_to(SOURCE.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@cache
def modules() -> dict[str, Path]:
    """모듈 이름 -> 파일. 패키지의 파일은 __init__.py 다."""
    return {module_name(path): path for path in sorted(SOURCE.rglob("*.py"))}


def is_package(module: str) -> bool:
    return modules()[module].name == "__init__.py"


def packages() -> list[str]:
    return [name for name in modules() if is_package(name)]


def parse(module: str) -> ast.Module:
    return ast.parse(modules()[module].read_text(encoding="utf-8"))


def line_count(module: str) -> int:
    return len(modules()[module].read_text(encoding="utf-8").splitlines())


def layer_of(module: str) -> str:
    """frame.<계층>... 의 계층 이름. frame 자신은 '(root)', frame 밖은 '(external)'."""
    parts = module.split(".")
    if parts[0] != PACKAGE:
        return EXTERNAL
    return parts[1] if len(parts) > 1 else TOP


def _resolve(importer: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    base = importer.split(".")
    if not is_package(importer):
        base = base[:-1]
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, *([node.module] if node.module else [])])


@cache
def imports() -> tuple[Import, ...]:
    found: dict[Import, None] = {}
    known = modules()
    for importer, path in known.items():
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(parse(importer)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found[Import(importer, alias.name, rel, node.lineno)] = None
            elif isinstance(node, ast.ImportFrom):
                base = _resolve(importer, node)
                for alias in node.names:
                    submodule = f"{base}.{alias.name}"
                    target = submodule if submodule in known else base
                    found[Import(importer, target, rel, node.lineno)] = None
    return tuple(found)


def internal_imports() -> list[Import]:
    return [item for item in imports() if layer_of(item.imported) != EXTERNAL]


def external_imports() -> list[Import]:
    return [item for item in imports() if layer_of(item.imported) == EXTERNAL]


def listing(items: list[Import]) -> str:
    return "\n" + "\n".join(f"  {item}" for item in items)
