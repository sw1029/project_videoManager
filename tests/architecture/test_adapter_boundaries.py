"""어댑터 경계 (ADR-0030): 어댑터 패키지는 서로를 import 하지 않는다. 함께 쓰는 것은 adapters/shared/ 에 둔다.

adapters/shared/ 로 옮기기 전의 공용 모듈은 baselines.LEGACY_SHARED_ADAPTER_MODULES 에 있다.
"""

from __future__ import annotations

from tests.architecture.baselines import LEGACY_SHARED_ADAPTER_MODULES
from tests.architecture.imports import internal_imports, listing, modules

ADAPTERS = "frame.adapters"
SHARED = "frame.adapters.shared"


def _adapter_package(module: str) -> str | None:
    """frame.adapters.<패키지>... 의 <패키지>. 어댑터 최상위의 모듈(공용 모듈)과 어댑터 밖은 None."""
    parts = module.split(".")
    if len(parts) < 3 or ".".join(parts[:2]) != ADAPTERS:
        return None
    return parts[2] if _is_package(".".join(parts[:3])) else None


def _is_package(module: str) -> bool:
    path = modules().get(module)
    return path is not None and path.name == "__init__.py"


def _is_shared(module: str) -> bool:
    return (
        module == SHARED
        or module.startswith(SHARED + ".")
        or module in LEGACY_SHARED_ADAPTER_MODULES
    )


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


def test_shared_modules_outside_the_shared_package_are_the_known_ones():
    """어댑터 최상위에 놓인 모듈은 기준선에 적힌 것뿐이다. 새 공용 모듈은 adapters/shared/ 에 만든다."""
    top_level = {
        name
        for name in modules()
        if name.startswith(ADAPTERS + ".") and name.count(".") == 2 and not _is_package(name)
    }

    assert top_level == set(LEGACY_SHARED_ADAPTER_MODULES)
