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
