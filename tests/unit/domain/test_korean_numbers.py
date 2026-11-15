"""한국어의 수 읽기 (ADR-0028): 적은 수와 읽는 소리를 같은 값으로 읽는다. 수량(값+단위)은 test_quantities.py."""

from __future__ import annotations

from decimal import Decimal

import pytest

from frame.domain.script import parse_number, read_number


@pytest.mark.parametrize(
    ("digits", "spoken"),
    [
        ("5,000", "오천"),
        ("4500", "사천오백"),
        ("10", "십"),
        ("11", "십일"),
        ("100", "백"),
        ("12000", "만이천"),
        ("15000", "만오천"),
        ("20,000", "이만"),
        ("110000", "십일만"),
        ("1500000", "백오십만"),
        ("100000000", "일억"),
        ("0", "영"),
        ("3", "삼"),
    ],
)
def test_digits_are_read_as_sino_korean(digits, spoken):
    assert read_number(digits) == spoken


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("오천", 5000),
        ("사천오백", 4500),
        ("만 이천", 12000),
        ("삼만오천", 35000),
        ("천", 1000),
        ("백오십만", 1_500_000),
        ("이십오", 25),
        ("십", 10),
        ("5,000", 5000),
        ("4500", 4500),
        ("3천", 3000),
        ("1만 2천", 12000),
        ("2억 5천만", 250_000_000),
        ("한", 1),
        ("두", 2),
        ("열두", 12),
        ("스물한", 21),
        ("스무", 20),
        ("서른다섯", 35),
    ],
)
def test_spoken_and_written_numbers_have_the_same_value(text, value):
    assert parse_number(text) == Decimal(value)


def test_decimals_are_read_digit_by_digit_after_the_point():
    assert parse_number("2.5") == Decimal("2.5")
    assert parse_number("이 점 오") == Decimal("2.5")
    assert parse_number("삼 점 일사") == Decimal("3.14")
    assert parse_number("일 쩜 오") == Decimal("1.5")  # '쩜' 은 말할 때의 소수점이다


def test_numbers_written_word_by_word_are_one_number():
    """받아 적은 글은 수를 낱말마다 띄어 적기도 한다 (Zeroth-Korean 의 대본이 그렇다)."""
    assert parse_number("이천 십 이") == Decimal(2012)
    assert parse_number("일 조 이천 구백 십 구 억") == Decimal(1_291_900_000_000)
    assert parse_number("마흔 여섯") == Decimal(46)
    assert parse_number("열 두") == Decimal(12)
    assert parse_number("오 쩜 구 사") == Decimal("5.94")


@pytest.mark.parametrize(
    "text", ["", "메뉴", "오늘", "천오천", "십백", "5,000원", "두세", "조", "억"]
)
def test_what_is_not_one_number_has_no_value(text):
    assert parse_number(text) is None
