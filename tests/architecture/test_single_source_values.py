"""같은 글자 값은 한 곳에만 적는다 (ADR-0032 의 이웃: 값의 사본을 두지 않는다).

여러 모듈이 같은 글자로 맞춰야 하는 값이 있다 — 기록을 읽는 쪽이 그 글자로 찾는 오류 코드, 여러 입구가 같은 식으로
검사해야 하는 키 · id 의 규칙. 쓰는 곳마다 다시 적으면 한쪽만 바뀌어도 어느 검사에도 걸리지 않는다
(테마의 키 규칙은 IR 의 것을 베낀 사본이었고, 저장소의 id 규칙은 테마의 것과 손으로 맞춘 것이었다 — 감사 P-20).

아래 표의 값은 '집' 모듈에 한 번만 글자로 적는다. 다른 곳은 그 이름을 가져다 쓴다. 테스트는 표의 대상이 아니다:
테스트는 밖에서 보이는 글자(기록의 오류 코드)를 그대로 적어 확인한다.
"""

from __future__ import annotations

import ast
from typing import NamedTuple

import pytest

from tests.architecture.imports import ROOT, modules, parse


class Home(NamedTuple):
    module: str
    name: str  # 그 값을 담은 이름 (다른 곳은 이것을 가져다 쓴다)


SINGLE_SOURCE: dict[str, Home] = {
    # 예상하지 못한 예외의 코드: 파이프라인의 실패 기록 · 평가의 케이스 결과 · 검수 단위의 미판정 사유
    "UNEXPECTED_ERROR": Home("frame.application.errors", "UNEXPECTED_ERROR"),
    # 객체 · 장면 키의 형식. 테마의 자리 키 · 고정 출연 키도 같은 규칙이다 (IR 의 키가 된다)
    r"[A-Za-z0-9][A-Za-z0-9_]{0,63}": Home("frame.domain.ir", "KEY_PATTERN"),
    # 테마 · 편 문서의 id 형식. 도메인이 검사하는 것과 저장소가 받는 것이 같아야 한다
    r"[a-z0-9][a-z0-9_-]{0,63}": Home("frame.domain.series.ids", "DOCUMENT_ID_PATTERN"),
    # LLM 주소를 주지 않았을 때 요청이 가는 곳. LLM 클라이언트가 SDK 에 넘기는 주소와, 조립이 '음성 인식은
    # LLM 과 같은 곳' 이라고 할 때의 주소가 같아야 한다 (감사 BTS-D05)
    "https://api.openai.com/v1": Home("frame.adapters.llm.openai_client", "OPENAI_BASE_URL"),
}


def _written(module: str) -> list[tuple[str, int]]:
    """그 모듈이 글자로 적은 표의 값과 줄 번호 (문서 문자열 · 주석 속의 언급은 값이 아니다)."""
    return [
        (node.value, node.lineno)
        for node in ast.walk(parse(module))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in SINGLE_SOURCE
    ]


def test_shared_values_are_written_in_one_place():
    copies = []
    for module, path in modules().items():
        for value, line in _written(module):
            home = SINGLE_SOURCE[value]
            if module != home.module:
                where = path.relative_to(ROOT).as_posix()
                copies.append(f"{where}:{line}  {value!r}  ->  {home.module}.{home.name}")

    if copies:
        pytest.fail(
            "같은 값을 다시 적었다. 그 값의 이름을 가져다 쓴다:\n  " + "\n  ".join(copies),
            pytrace=False,
        )


@pytest.mark.parametrize("value", sorted(SINGLE_SOURCE))
def test_each_value_is_written_once_in_its_home(value):
    """표가 낡지 않게: 집 모듈이 있고, 그 값을 한 번 적었고, 표가 말한 이름이 그 값을 담고 있다."""
    home = SINGLE_SOURCE[value]

    assert home.module in modules(), home.module
    assert [found for found, _ in _written(home.module)].count(value) == 1
    assigned = {
        target.id
        for node in ast.walk(parse(home.module))
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert home.name in assigned, f"{home.module} 에 {home.name} 이 없다"
