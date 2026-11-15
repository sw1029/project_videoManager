"""규칙 기반 구조화기의 입력 DSL 가장자리 (감사 A-10, A-26): 객체 종류, 주석의 '#', 장면 목록의 '@'."""

from __future__ import annotations

import pytest

from frame.adapters.structurer import RuleBasedStructurer
from frame.application.errors import StructuringError
from frame.application.ports.structurer import StructuringRequest
from frame.domain.enums import EntityKind

HEAD = (
    "scene a 0-1000\nscene b 1000-2000\nentity cup PROP\nentity sign TEXT\n"
    "in a: cup, sign\nin b: cup, sign\n"
)


def _structure(dsl: str):
    return RuleBasedStructurer().structure(StructuringRequest(dsl))


def _values(line: str) -> dict[str, str]:
    """HEAD 뒤에 line 을 붙여 구조화한 요구조건: {키: 값}."""
    return {r.key: r.expected_value for r in _structure(HEAD + line).iter_requirements()}


# ---- 객체 종류 (A-10) ------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(EntityKind), ids=str)
def test_every_entity_kind_can_be_written(kind):
    """종류 목록을 따로 적어 두면 종류가 더해질 때 어긋난다 (ANIMAL 이 빠져 있었다)."""
    ir = _structure(f"scene a 0-1000\nentity pet {kind}: 설명\nin a: pet")

    assert ir.entity("pet").kind is kind
    assert ir.entity("pet").description == "설명"


def test_an_unknown_kind_is_still_rejected():
    with pytest.raises(StructuringError, match="해석할 수 없는 문장"):
        _structure("scene a 0-1000\nentity pet ROBOT: 설명\nin a: pet")


# ---- 주석과 '#' (A-26 c) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "key", "value"),
    [
        ("cup.color = #ff0000", "a:cup:color", "#ff0000"),
        ("cup.color = #ff0000 # 빨강", "a:cup:color", "#ff0000"),
        ("cup.color = #f00", "a:cup:color", "#f00"),
        ("cup.color = red # 주석", "a:cup:color", "red"),
        ("cup.color = red #주석", "a:cup:color", "red"),
        ("cup.color = red #cafe 톤으로", "a:cup:color", "red"),
        ("sign.text = A#1", "a:sign:text", "A#1"),
        ("sign.text = OPEN # 간판 문구", "a:sign:text", "OPEN"),
    ],
)
def test_a_hash_is_a_comment_unless_it_is_part_of_the_value(line, key, value):
    """어휘는 색을 '색 이름/HEX' 로 적는다. '#' 뒤를 무조건 주석으로 자르면 HEX 색을 쓸 수 없다."""
    assert _values(line)[key] == value


def test_a_line_that_starts_with_a_hash_is_always_a_comment():
    ir = _structure("# 카페 장면\n   #cafe 메모\n" + HEAD + "cup.color = red")

    assert [r.key for r in ir.iter_requirements()] == ["a:cup:color", "b:cup:color"]


# ---- 장면 목록과 '@' (A-26 d) ----------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "sign.text = info@frame.io",
            {"a:sign:text": "info@frame.io", "b:sign:text": "info@frame.io"},
        ),
        ("sign.text = info@frame.io @ a", {"a:sign:text": "info@frame.io"}),
        ("cup.color = red @ b", {"b:cup:color": "red"}),
        ("cup.color = red @b", {"b:cup:color": "red"}),
        ("cup.color = red @ a, b", {"a:cup:color": "red", "b:cup:color": "red"}),
    ],
)
def test_an_at_sign_inside_a_value_is_not_a_scene_list(line, expected):
    """장면 목록은 값과 띄어 쓴 '@' 뒤에 온다. 값에 붙어 있는 '@'(메일 주소 등)는 값의 일부다."""
    assert _values(line) == expected


def test_a_scene_list_that_names_an_unknown_scene_is_an_error():
    with pytest.raises(StructuringError, match="정의되지 않은 장면"):
        _values("cup.color = red @ nowhere")
