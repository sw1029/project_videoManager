"""정확히 고정한 의존성(pyproject 의 `이름==판`)을 설치된 판과 견준다 (ADR-0033).

고정한 것과 다른 판이 설치돼 있으면 로컬의 검사 결과가 CI 와 달라진다: 포맷 · 타입 검사, OpenAPI 명세, 응답의 모양.
여러 작업이 한 가상 환경을 같이 쓰면 고정을 올린 뒤에 환경이 뒤처지기 쉽다. 그때 무엇을 할지 알려 준다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping

_EXACT = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;,]+)\s*")


def exact_pins(requirements: Iterable[str]) -> dict[str, str]:
    """`이름==판` 으로 적은 요구만 고른다. 범위로 적은 것과 조건(;)이 붙은 것은 고정이 아니다."""
    pins: dict[str, str] = {}
    for requirement in requirements:
        match = _EXACT.fullmatch(requirement)
        if match is not None:
            pins[match.group(1)] = match.group(2)
    return pins


def pin_problems(
    required: Mapping[str, str],
    optional: Mapping[str, str],
    installed: Callable[[str], str | None],
) -> list[str]:
    """고정과 어긋난 것. required 는 설치돼 있어야 하고, optional(선택 설치)은 설치돼 있을 때만 판을 본다."""
    problems: list[str] = []
    for name, pinned in {**optional, **required}.items():
        version = installed(name)
        if version is None:
            if name in required:
                problems.append(f"{name}: 설치되지 않음 (고정 {pinned})")
        elif version != pinned:
            problems.append(f"{name}: 설치된 판 {version}, 고정 {pinned}")
    return sorted(problems)
