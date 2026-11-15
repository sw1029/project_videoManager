"""어댑터 경계 (ADR-0030): 어댑터 패키지는 서로를 import 하지 않는다. 함께 쓰는 것은 adapters/shared/ 에 둔다.

어댑터 최상위에는 패키지만 둔다 — 공용 모듈은 adapters/shared/ 에 만든다 (ADR-0031).
옛 경로에 남겼던 호환 모듈은 걷어냈다 (tests/architecture/test_moved_paths.py 가 다시 생기지 않게 지킨다).
"""

from __future__ import annotations

from tests.architecture.imports import internal_imports, listing, modules

ADAPTERS = "frame.adapters"
SHARED = "frame.adapters.shared"


def _adapter_package(module: str) -> str | None:
    """frame.adapters.<패키지>... 의 <패키지>. 어댑터 밖은 None."""
    parts = module.split(".")
    if len(parts) < 3 or ".".join(parts[:2]) != ADAPTERS:
        return None
    return parts[2] if _is_package(".".join(parts[:3])) else None


def _is_package(module: str) -> bool:
    path = modules().get(module)
    return path is not None and path.name == "__init__.py"


def _is_shared(module: str) -> bool:
    return module == SHARED or module.startswith(SHARED + ".")


def test_adapter_packages_do_not_import_each_other():
    wrong = []
    for item in internal_imports():
        source, target = _adapter_package(item.importer), _adapter_package(item.imported)
        if source is None or target is None or source == target or source == "shared":
            continue
        if not _is_shared(item.imported):
            wrong.append(item)

    assert not wrong, "어댑터 패키지끼리의 import (함께 쓰는 것은 adapters/shared/ 로):" + listing(
        wrong
    )


def test_there_are_no_modules_at_the_top_of_adapters():
    """어댑터 최상위에는 패키지만 있다. 함께 쓰는 모듈은 adapters/shared/ 에 만든다."""
    top_level = sorted(
        name
        for name in modules()
        if name.startswith(ADAPTERS + ".") and name.count(".") == 2 and not _is_package(name)
    )

    assert top_level == [], "어댑터 최상위의 모듈 (adapters/shared/ 나 그 개념의 패키지로 옮긴다)"
