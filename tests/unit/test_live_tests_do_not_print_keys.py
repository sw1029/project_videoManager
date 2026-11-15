"""유료 테스트(tests/live)의 '키가 결과에 없다' 단언은 실패해도 키를 출력하지 않아야 한다 (감사 T-08).

`assert key not in text` 가 실패하면 pytest 는 양쪽 값을 보고서에 싣는다. 키 유출을 잡으려던 단언이 실패하는
순간 실제 키가 터미널과 CI 로그에 남는다. 값을 먼저 계산해 `assert not leaked, "..."` 로 써야 한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

LIVE = Path(__file__).resolve().parents[1] / "live"
# 실제 키가 담기는 변수 이름 (resolve_*_key 의 결과를 받는 이름)
SECRET_NAMES = {"key", "api_key", "secret", "token"}


def _compares_a_secret(test: ast.expr) -> bool:
    """단언의 식이 비밀 변수를 직접 견주는가 (in · not in · == · !=)."""
    for node in ast.walk(test):
        if not isinstance(node, ast.Compare):
            continue
        for operand in (node.left, *node.comparators):
            if isinstance(operand, ast.Name) and operand.id in SECRET_NAMES:
                return True
    return False


def test_live_tests_never_assert_on_a_real_key_directly():
    offenders = []
    for path in sorted(LIVE.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [
            f"{path.name}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert) and _compares_a_secret(node.test)
        ]

    assert not offenders, (
        "실패하면 실제 키가 출력되는 단언 (leaked = key in text; assert not leaked 로 쓴다): "
        + ", ".join(offenders)
    )


def test_the_check_recognises_the_unsafe_and_the_safe_way():
    unsafe = ast.parse("assert key not in dumped").body[0]
    safe = ast.parse("leaked = key in dumped\nassert not leaked, 'x'").body[1]

    assert isinstance(unsafe, ast.Assert) and _compares_a_secret(unsafe.test)
    assert isinstance(safe, ast.Assert) and not _compares_a_secret(safe.test)
