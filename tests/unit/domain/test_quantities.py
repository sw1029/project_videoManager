"""수량 (ADR-0028): 값과 단위. 말한 '오천 원' 과 적은 '5,000원' 을 값으로 견준다.

뒤쪽의 '낱말을 수량으로 읽지 않는다' 묶음은 오픈 말뭉치의 대본(KSS · Zeroth-Korean · FLEURS, 13,919문장)에서
실제로 잘못 읽은 것들이다 (docs/research/theme-series.md §8.7).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from frame.domain.script import (
    canonical_text,
    mentions,
    quantities,
    quantity_conflicts,
    shared_quantities,
)


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


def test_the_same_words_written_apart_are_quantities():
    assert _pairs("십 분 동안 끓이세요") == [(Decimal(10), "분")]
    assert _pairs("한 시에 만나요") == [(Decimal(1), "시")]
    # 붙여 써도 낱말이 아닌 것은 수량이다
    assert _pairs("단돈 만원입니다") == [(Decimal(10000), "원")]


def test_the_longest_number_before_a_unit_wins_and_days_backtrack():
    assert _pairs("이십 일 동안") == [(Decimal(20), "일")]
    assert _pairs("이십일 일 동안") == [(Decimal(21), "일")]
    assert _pairs("삼 일") == [(Decimal(3), "일")]


def test_the_colloquial_decimal_point_is_read_too():
    assert _pairs("용량은 일 점 오 리터") == [(Decimal("1.5"), "l")]
    # 받아 적은 글은 소수 자리를 띄어 적기도 한다 (Zeroth-Korean)
    assert _pairs("평균 체류기간은 오 쩜 구 사 시간") == [(Decimal("5.94"), "시간")]


# ---- 견주기 ----------------------------------------------------------------------
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


def test_mentions_compares_quantities_by_value():
    assert mentions("가격은 오천 원이에요", "5,000원")
    assert not mentions("가격은 육천 원이에요", "5,000원")


# ---- 붙여 쓴 수량 ----------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("오천원", [(5000, "원")]),
        ("삼만원에 드려요", [(30000, "원")]),
        ("이십분이면 됩니다", [(20, "분")]),
        ("단돈 천원", [(1000, "원")]),
        ("한개 더", [(1, "개")]),
        ("두명이 왔어요", [(2, "명")]),
        (
            "커피 한잔에 이천원 두개 사면 삼천오백원",
            [(1, "잔"), (2000, "원"), (2, "개"), (3500, "원")],
        ),
        ("우리 클럽은 일주일에 두 번 모입니다", [(1, "주"), (2, "번")]),
    ],
)
def test_numbers_of_two_or_more_syllables_may_be_written_together_with_the_unit(text, expected):
    assert _pairs(text) == [(Decimal(value), unit) for value, unit in expected]


# ---- 낱말을 수량으로 읽지 않는다 --------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "범죄율의 증가는 불안정한 사회를 반영합니다",  # 사회 ≠ 4회
        "그는 그 회사의 사장이라고 거짓말했다",  # 사장 ≠ 4장
        "진짜인지 가짜인지 구분이 안 돼요",  # 구분 ≠ 9분
        "사위는 변호사예요",  # 사위 ≠ 4위
        "건강해야 일도 할 수 있죠",  # 일도 ≠ 1도
        "경찰에 신고하려면 일일이를 누르세요",  # 일일 ≠ 1일
        "올리브 오일을 두르고",  # 오일 ≠ 5일
        "신입 사원입니다",
        "구원 투수가 나왔다",
        "이제는 아무도 이 거리를 오가지 않습니다",  # 오가지 ≠ 5가지
        "후임 사장으로 백 사장이 아니라 다른 후보를 원했다",  # 백 사장 ≠ 104장
    ],
)
def test_a_single_sino_digit_written_together_with_a_unit_is_often_a_word(text):
    """한 글자 한자어 수에 단위가 붙은 꼴 가운데 낱말인 것: 원·도·세 앞, 고유어 수로 세는 단위 앞, 낱말 목록."""
    assert quantities(text) == ()


@pytest.mark.parametrize(
    "text",
    [
        "육회 한 접시",  # 육회 ≠ 6회
        "세계 일주를 떠났다",  # 일주 ≠ 1주
        "사주팔자",
        "오점을 남겼다",
        "전국 팔도",
        "화면 구도가 좋다",
        "구위가 좋은 투수",
    ],
)
def test_more_words_made_of_a_single_digit_and_a_unit(text):
    assert quantities(text) == ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("지난해 삼월 김 전 장관의 동료", [(3, "월")]),
        ("천구백사십년 팔월 십오일 연합군은", [(1940, "년"), (8, "월"), (15, "일")]),
        ("앞으로 오년 밖에 남지 않은", [(5, "년")]),
        ("위민관 삼층 영상국무회의실", [(3, "층")]),
        ("화재 당시 일층으로 추락했던", [(1, "층")]),
        ("지난 구월 구일에도", [(9, "월"), (9, "일")]),
        ("사일 새벽 두 명 더 추가되면서", [(4, "일"), (2, "명")]),
        ("반에서 일등을 했다", [(1, "등")]),
        ("이천 십이년 맥북", [(2012, "년")]),
    ],
)
def test_dates_floors_and_years_written_together_are_read(text, expected):
    """받아 적은 글은 '삼월'·'오년'·'삼층' 처럼 붙여 적는다 (Zeroth-Korean · FLEURS 를 받아 적은 결과)."""
    assert _pairs(text) == [(Decimal(value), unit) for value, unit in expected]


def test_the_same_single_digit_written_apart_is_a_quantity():
    assert _pairs("오 분마다 진통을 느꼈다") == [(Decimal(5), "분")]
    assert _pairs("엘리베이터를 타고 오 층에서 내리세요") == [(Decimal(5), "층")]
    assert _pairs("일 분은 육십 초다") == [(Decimal(1), "분"), (Decimal(60), "초")]


@pytest.mark.parametrize(
    "text",
    [
        "이 약은 아이들 손이 닿지 않는 곳에 두세요",  # 두세(요) ≠ 2세
        "시간 있으면 제 전시회에 오세요",  # 오세(요) ≠ 5세
        "많이 사세요",  # 사세(요) ≠ 4세
        "대중교통으로 오시는 게 더 편리하실 거예요",  # 오시(는) ≠ 5시
        "여기에 두시면 됩니다",  # 두시(면) ≠ 2시
        "가방은 거기 두시고 가세요",
        "인형을 사 달라고 조르고 있어요",  # 사 달(라고) ≠ 4달
        "하나 사 주면 좋겠다",  # 사 주(면) ≠ 4주
        "아버지는 술이 굉장히 세세요",
    ],
)
def test_verb_endings_are_not_units(text):
    assert quantities(text) == ()


def test_longer_numbers_that_make_a_word_with_a_unit():
    assert quantities("청와대 등은 백사장이 아니라 다른 후보를 원했다") == ()  # 백사장 ≠ 104장
    assert quantities("백일장에 나갔다") == ()


def test_a_listed_word_is_a_quantity_when_a_duration_follows():
    assert _pairs("첫 신고 전 십분 사이에 건물 출입구로") == [(Decimal(10), "분")]
    assert quantities("십분 활용하세요") == ()


def test_times_written_together_are_still_read():
    assert _pairs("두시에 만나요") == [(Decimal(2), "시")]
    assert _pairs("세시 반까지 오세요") == [(Decimal(3), "시")]
    assert _pairs("사 주 동안 이어집니다") == [(Decimal(4), "주")]


@pytest.mark.parametrize(
    "text",
    [
        "오늘 스포츠용품 특별 세일 중이거든요",  # 세일 ≠ 3일
        "네일 아트 예약",  # 네일 ≠ 4일
        "자신이 한 일에 대한 후회",  # 한 일 ≠ 1일
        "한도 초과입니다",  # 한도 ≠ 1도
        "하나도 없어요",  # 하나도 ≠ 1도
        "두 분이 병원에 입원하셨어요",  # 두 분은 사람이다 (2분이 아니다)
        "일본 열도",  # 열도 ≠ 10도
        "열세에 몰렸다",  # 열세 ≠ 10세
    ],
)
def test_a_native_number_does_not_take_a_unit_counted_in_sino_numbers(text):
    """원·년·월·일·분·세·도·위·등은 한자어 수로만 센다 ('한 일'·'세 일' 은 수량이 아니다)."""
    assert quantities(text) == ()


@pytest.mark.parametrize(
    "text",
    [
        "날씨가 한층 시원해졌어요",
        "사진을 찍을 때 한쪽 눈을 감는 버릇",
        "천장이 너무 낮아서 좀 갑갑해요",
        "신랑은 결혼식에서 만세를 외쳤다",
        "만주 벌판",
        "만일 일이 잘못되면 다 네 탓이야",
        "만일 그렇다면",
        "만일의 경우를 생각해서 돈을 좀 갖고 가라",
        "십분 활용하세요",
        "한시도 쉬지 않고",
        "가까운 시일 내에 한번 찾아가겠습니다",
    ],
)
def test_common_words_made_of_a_round_or_native_number_and_a_unit_are_not_quantities(text):
    assert quantities(text) == ()


@pytest.mark.parametrize(
    "text",
    [
        "수많은 인파가 공원에 모여 있다",  # 공원 ≠ 0원
        "그 공장은 최신 설비를 갖추고 있다",
        "범인의 사진이 언론에 공개 되었다",
        "제 아내는 자기가 공주인 줄 알아요",
        "나 영장 나왔어",
    ],
)
def test_a_spelled_zero_does_not_start_a_quantity(text):
    assert quantities(text) == ()


def test_zero_is_read_in_digits_and_before_a_decimal_point():
    assert _pairs("배송비 0원") == [(Decimal(0), "원")]
    assert _pairs("영 점 오 퍼센트") == [(Decimal("0.5"), "%")]


@pytest.mark.parametrize(
    "text",
    [
        "저는 그 공연의 조명을 맡고 있어요",  # 조명 ≠ 1조 명
        "조세 개정에 반대해",
        "제 블로그 조회 수는",
        "조개를 구웠다",
    ],
)
def test_a_bare_big_unit_is_not_a_number(text):
    """'만 원'·'천 원' 은 수이지만 '조'·'억' 은 앞에 수가 있어야 한다."""
    assert quantities(text) == ()


def test_big_units_after_a_number_are_read():
    assert _pairs("매출 일 조 이천 구백 십 구 억원") == [(Decimal(1_291_900_000_000), "원")]
    assert _pairs("삼억 원") == [(Decimal(300_000_000), "원")]


# ---- 놓치던 것 --------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("백 원으로는 사탕 하나도 살 수가 없어요", [(100, "원")]),
        ("사망자 수가 삼십 명으로 늘어났다", [(30, "명")]),
        ("가계신용 잔액은 천 삼백 사십 사 조 삼천 억원으로", [(1_344_300_000_000_000, "원")]),
        ("오늘 오후 세 시경 지진이 발생했다", [(3, "시")]),
        ("방이 세 개고 거실이 한 개다", [(3, "개"), (1, "개")]),
        ("유권자 네 명 중 한 명꼴로 기권했다", [(4, "명"), (1, "명")]),
        ("매장보다 천 원어치 더 담았어요", [(1000, "원")]),
        ("만 원보다 싸요", [(10000, "원")]),
        ("그동안 팔 년동안 국내에 체류하면서", [(8, "년")]),
    ],
)
def test_particles_after_a_unit(text, expected):
    assert _pairs(text) == [(Decimal(value), unit) for value, unit in expected]


def test_native_tens_and_ones_written_apart_are_one_number():
    assert _pairs("관련자를 마흔 여섯 명이나 불러서") == [(Decimal(46), "명")]
    assert _pairs("스물 한 살") == [(Decimal(21), "살")]
    assert _pairs("밤 열 두 시") == [(Decimal(12), "시")]


def test_months_are_units():
    assert _pairs("십일 월 팔 일은 결혼 기념일이에요") == [(Decimal(11), "월"), (Decimal(8), "일")]
    assert _pairs("11월 8일") == [(Decimal(11), "월"), (Decimal(8), "일")]
    assert _pairs("십 일 월 삼 일") == [(Decimal(11), "월"), (Decimal(3), "일")]
    # 유월·시월은 읽는 소리가 다르다
    assert _pairs("시월 팔 일까지, 유월 십오 일부터") == [
        (Decimal(10), "월"),
        (Decimal(8), "일"),
        (Decimal(6), "월"),
        (Decimal(15), "일"),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "최종 점수는 십일 대 팔이었어",  # 11 대 8
        "이십일 세기의 대중문화",  # 21세기
        "백일 호인데요, 주문 좀 하고 싶어요",  # 101호
    ],
)
def test_a_number_ending_in_one_is_not_split_into_a_number_and_days(text):
    """'십일' 은 11 이기도 하고 '십 일'(10일)이기도 하다. 뒤에 날수를 말하는 낱말이 없으면 수로 둔다."""
    assert quantities(text) == ()


def test_days_written_together_are_read_when_what_follows_says_so():
    assert _pairs("십일까지 접수합니다") == [(Decimal(10), "일")]
    assert _pairs("삼십일 동안") == [(Decimal(30), "일")]
    assert _pairs("이벤트는 백일 이내") == [(Decimal(100), "일")]


def test_a_lone_i_is_a_number_only_before_a_measure_or_a_duration():
    """혼자 선 '이' 는 대개 가리키는 말이다 (이 분 · 이 일 · 이 잔)."""
    assert _pairs("깊이가 이 미터 정도 됩니다") == [(Decimal(2), "m")]
    assert _pairs("육군에서 이 개월 동안 복무했습니다") == [(Decimal(2), "개월")]
    assert _pairs("그는 언제나 이 인분을 시킨다") == [(Decimal(2), "인분")]
    assert _pairs("이 년간의 연구의 성과입니다") == [(Decimal(2), "년")]
    assert _pairs("증상이 이 주 이상 지속되면") == [(Decimal(2), "주")]
    assert quantities("이 분은 누구세요") == ()
    assert quantities("이 일은 어렵다") == ()
    assert quantities("이 잔에 따라 주세요") == ()


def test_a_fraction_is_not_minutes():
    assert quantities("4분의 1 가격") == ()
    assert quantities("삼 분의 일만 남았다") == ()
    assert _pairs("삼십 분의 휴식") == [(Decimal(30), "분")]


def test_hours_and_months_have_a_range():
    assert quantities("이천시에 삽니다") == ()  # 이천시 ≠ 2000시
    assert _pairs("이십사 시") == [(Decimal(24), "시")]
    assert quantities("십삼 월") == ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("단돈 10달러", [(10, "달러")]),
        ("별점 4.8점", [("4.8", "점")]),
        ("초등학교 3학년", [(3, "학년")]),
        ("창립 10주년", [(10, "주년")]),
        ("유럽 3개국 순방", [(3, "개국")]),
    ],
)
def test_more_units(text, expected):
    assert _pairs(text) == [(Decimal(value), unit) for value, unit in expected]


def test_a_full_score_is_a_word():
    assert _pairs("백점 만점에 구십 점") == [(Decimal(100), "점"), (Decimal(90), "점")]
