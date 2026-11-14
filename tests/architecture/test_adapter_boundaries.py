"""어댑터 경계 (ADR-0030): 어댑터 패키지는 서로를 import 하지 않는다. 함께 쓰는 것은 adapters/shared/ 에 둔다.

공용 모듈은 adapters/shared/ 로 옮겼다 (ADR-0031). 옛 경로에는 이름만 다시 내보내는 호환 모듈이 남아 있다
(baselines.COMPAT_ADAPTER_MODULES): 새 코드는 쓰지 않고, 구현을 담지 않는다.
"""

from __future__ import annotations

import ast

from tests.architecture.baselines import COMPAT_ADAPTER_MODULES, COMPAT_PATH_IMPORTERS
from tests.architecture.imports import internal_imports, listing, modules, parse

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
        or module in COMPAT_ADAPTER_MODULES  # 작업 중인 브랜치의 어댑터가 머지될 때까지
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


def test_modules_at_the_top_of_adapters_are_only_the_compat_ones():
    """어댑터 최상위에 놓인 모듈은 기준선에 적힌 호환 모듈뿐이다. 새 공용 모듈은 adapters/shared/ 에 만든다."""
    top_level = {
        name
        for name in modules()
        if name.startswith(ADAPTERS + ".") and name.count(".") == 2 and not _is_package(name)
    }

    assert top_level == set(COMPAT_ADAPTER_MODULES)


def test_compat_modules_only_re_export_names_from_their_new_home():
    """호환 모듈은 구현을 담지 않는다: 새 자리에서 가져온 이름을 __all__ 로 다시 내보내기만 한다."""
    wrong = []
    for old, new in COMPAT_ADAPTER_MODULES.items():
        body = parse(old).body
        defined = [
            node.name
            for node in body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        ]
        sources = [node.module or "" for node in body if isinstance(node, ast.ImportFrom)]
        elsewhere = [m for m in sources if m != new and not m.startswith(new + ".")]
        if defined or elsewhere or not sources:
            wrong.append(f"{old}: 정의 {defined}, {new} 밖에서 가져옴 {elsewhere}")

    assert not wrong, "호환 모듈이 구현을 담거나 새 자리 밖의 이름을 내보낸다:\n" + "\n".join(wrong)


def test_only_the_known_modules_still_import_the_compat_paths():
    """새 코드는 옛 경로를 쓰지 않는다. 아직 쓰는 모듈(다른 브랜치가 고치는 파일)은 기준선에 있다."""
    users = {
        item.importer for item in internal_imports() if item.imported in COMPAT_ADAPTER_MODULES
    }

    assert users == set(COMPAT_PATH_IMPORTERS)
