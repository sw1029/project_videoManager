"""한국어의 수 읽기와 수량 (ADR-0028): 말한 수('오천 원')와 적은 수('5,000원')를 값으로 견준다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from frame.domain.script.korean_numbers import (
    canonical_text,
    parse_number,
    quantities,
    quantity_conflicts,
    read_number,
    shared_quantities,
)


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


@pytest.mark.parametrize("text", ["", "메뉴", "오늘", "천오천", "십백", "5,000원", "두세"])
def test_what_is_not_one_number_has_no_value(text):
    assert parse_number(text) is None


def _pairs(text: str) -> list[tuple[Decimal, str]]:
    return [(q.value, q.unit) for q in quantities(text)]


def test_a_quantity_is_a_number_with_a_unit():
    assert _pairs("가격은 오천 원입니다") == [(Decimal(5000), "원")]
    assert _pairs("말차 라떼 5,000원") == [(Decimal(5000), "원")]
    assert _pairs("두 잔에 만 원") == [(Decimal(2), "잔"), (Decimal(10000), "원")]
    assert _pairs("1만 5천원짜리 세트") == [(Decimal(15000), "원")]
    assert _pairs("3분만에 완성, 재료는 세 가지") == [(Decimal(3), "분"), (Decimal(3), "가지")]


def test_unit_names_are_unified():
    assert _pairs("30% 할인") == _pairs("삼십 퍼센트 할인") == _pairs("삼십 프로 할인")
    assert _pairs("200g") == _pairs("이백 그램") == [(Decimal(200), "g")]
    assert _pairs("500ml 한 병") == [(Decimal(500), "ml"), (Decimal(1), "병")]


def test_quantity_keeps_where_it_was_said():
    text = "가격은 오천 원입니다"
    (quantity,) = quantities(text)
    assert text[quantity.start : quantity.end] == "오천 원" == quantity.raw


@pytest.mark.parametrize(
    "text",
    [
        "이 메뉴는 오늘만",  # '이'·'오'·'만' 은 수가 아니다
        "일을 한다",
        "오 분야의 전문가",  # '분' 뒤에 낱말이 이어진다
        "1+1 행사",  # 단위가 없다
        "넘버 원",
        "열심히 만든 한국 요리",
    ],
)
def test_numbers_without_a_unit_are_not_quantities(text):
    assert quantities(text) == ()


def test_the_longest_number_before_a_unit_wins_and_days_backtrack():
    assert _pairs("이십 일 동안") == [(Decimal(20), "일")]
    assert _pairs("이십일 일 동안") == [(Decimal(21), "일")]
    assert _pairs("삼 일") == [(Decimal(3), "일")]


def test_same_value_in_speech_and_on_screen_is_shared_not_a_conflict():
    said, shown = "가격은 오천 원입니다", "말차 라떼 5,000원"

    assert [(q.value, q.unit) for q in shared_quantities(said, shown)] == [(Decimal(5000), "원")]
    assert quantity_conflicts(said, shown) == ()


def test_different_values_of_the_same_unit_conflict():
    """글자 유사도는 0.83 이다 ('오천 원' / '육천 원'). 값으로 견줘야 다른 줄 안다."""
    (conflict,) = quantity_conflicts("가격은 육천 원입니다", "말차 라떼 5,000원")

    assert conflict.unit == "원"
    assert conflict.left == (Decimal(6000),) and conflict.right == (Decimal(5000),)


def test_a_unit_on_one_side_only_is_not_a_conflict():
    assert quantity_conflicts("두 잔 주문하세요", "5,000원") == ()
    # 한쪽에 값이 더 있어도 같은 값이 하나라도 있으면 어긋남이 아니다
    assert quantity_conflicts("오천 원, 두 잔은 만 원", "5,000원") == ()


def test_canonical_text_writes_quantities_one_way():
    assert canonical_text("오천 원") == canonical_text("5,000원") == "5000원"
    assert (
        canonical_text("말차 라떼 오천 원!") == canonical_text("말차라떼 5천원") == "말차라떼5000원"
    )
    assert canonical_text("삼십 퍼센트") == canonical_text("30%") == "30%"
    assert canonical_text("Hello, 월드") == "hello월드"
