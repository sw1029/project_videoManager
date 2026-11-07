"""술어 어휘: 별칭 정규화 · 값 종류 · 관계 술어 판정 · 확장."""

import pytest

from frame.domain.errors import ValidationError
from frame.domain.vocabulary import (
    DEFAULT_VOCABULARY,
    PredicateSpec,
    ValueKind,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("color", "color"),
        ("Colour", "color"),
        ("색상", "color"),
        (" 색 ", "color"),
        ("holds", "holds"),
        ("들다", "holds"),
        ("shirt color", "shirt_color"),  # 미등록 술어도 정규화만 하고 허용
        ("Shirt-Color", "shirt_color"),
    ],
)
def test_normalize_aliases_and_unknown_predicates(raw, expected):
    assert DEFAULT_VOCABULARY.normalize(raw) == expected


def test_known_predicates_carry_value_kind_and_relational_flag():
    assert DEFAULT_VOCABULARY.value_kind("색상") is ValueKind.COLOR
    assert DEFAULT_VOCABULARY.is_relational("holds") is True
    assert DEFAULT_VOCABULARY.is_relational("color") is False
    assert DEFAULT_VOCABULARY.is_relational("shirt_color") is None  # 미등록 술어는 판정 보류
    assert DEFAULT_VOCABULARY.value_kind("shirt_color") is None


def test_empty_predicate_is_rejected():
    with pytest.raises(ValidationError):
        DEFAULT_VOCABULARY.normalize("   ")


def test_vocabulary_can_be_extended_without_touching_builtins():
    extended = DEFAULT_VOCABULARY.with_predicates(
        PredicateSpec("logo_position", ValueKind.POSITION, aliases=("로고 위치",))
    )
    assert extended.normalize("로고 위치") == "logo_position"
    assert DEFAULT_VOCABULARY.spec("logo_position") is None
    assert "color" in extended.names()


# ---- 값 정규화 (실호출에서 드러난 한계 보완) --------------------------------------------


@pytest.mark.parametrize(
    ("predicate", "raw", "expected"),
    [
        ("color", "  Red ", "red"),
        ("color", "#FF0000", "#ff0000"),
        ("outfit", "White_Shirt", "white shirt"),
        ("action", "wave   hand", "wave hand"),
        ("lighting", "bright_studio", "bright studio"),
        ("color", "빨강", "빨강"),
        ("text", "FRAME", "FRAME"),  # 화면 문구는 대소문자 그대로
        ("text", "  Hello World ", "Hello World"),
        ("brand", "Nike", "Nike"),
        ("count", "03", "3"),
        ("count", "2.0", "2"),
        ("position", "왼쪽 위", "top left"),
        ("position", "Center", "center"),
        ("holds", "cup", "cup"),  # 관계 값은 객체 키 그대로
        ("shirt_color", "Light_Blue", "light blue"),  # 미등록 술어는 TEXT 규칙
    ],
)
def test_normalize_value_by_value_kind(predicate, raw, expected):
    assert DEFAULT_VOCABULARY.normalize_value(predicate, raw) == expected


def test_literal_kind_and_new_predicates_exist():
    assert DEFAULT_VOCABULARY.value_kind("text") is ValueKind.LITERAL
    assert DEFAULT_VOCABULARY.value_kind("brand") is ValueKind.LITERAL
    for name in ("location", "hair", "age", "gender", "pose", "style", "time_of_day", "weather"):
        assert DEFAULT_VOCABULARY.spec(name) is not None, name
    assert DEFAULT_VOCABULARY.normalize("장소") == "location"
