"""패키지의 입구 (ADR-0030·감사 K-03): 뒤에 들어오는 사람이 패키지가 맡은 일과 밖에서 쓰는 이름을 바로 찾게 한다.

- 패키지 __init__.py 에는 설명(docstring)과 밖에서 쓰는 이름(__all__)이 있다.
- 이름이 `_` 로 시작하는 모듈은 그 패키지 안쪽의 구현이다. 패키지 밖에서 import 하지 않는다.
"""

from __future__ import annotations

import ast

from tests.architecture.baselines import PACKAGES_WITHOUT_ALL, PACKAGES_WITHOUT_DOCSTRING
from tests.architecture.imports import internal_imports, listing, packages, parse


def _declares_all(module: str) -> bool:
    for node in parse(module).body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            return True
    return False


def test_every_package_says_what_it_is_for():
    missing = {name for name in packages() if not ast.get_docstring(parse(name))}

    assert missing - PACKAGES_WITHOUT_DOCSTRING == set(), "설명이 없는 새 패키지"
    assert PACKAGES_WITHOUT_DOCSTRING - missing == set(), "이제 설명이 있으니 기준선에서 지운다"


def test_every_package_lists_its_public_names():
    missing = {name for name in packages() if not _declares_all(name)}

    assert missing - PACKAGES_WITHOUT_ALL == set(), "__all__ 이 없는 새 패키지"
    assert PACKAGES_WITHOUT_ALL - missing == set(), "이제 __all__ 이 있으니 기준선에서 지운다"


def test_private_modules_are_not_imported_from_outside_their_package():
    wrong = []
    for item in internal_imports():
        package, _, name = item.imported.rpartition(".")
        if not name.startswith("_") or name.startswith("__"):
            continue
        inside = item.importer == package or item.importer.startswith(package + ".")
        if not inside:
            wrong.append(item)

    assert not wrong, "패키지 밖에서 안쪽 모듈(_ 로 시작)을 import 했다:" + listing(wrong)
