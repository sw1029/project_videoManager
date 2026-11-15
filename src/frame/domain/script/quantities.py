"""수량 — 값과 단위 (ADR-0028). 순수 함수.

말로는 '오천 원', 화면에는 '5,000원' 이다. 글자로 견주면 다른 글이고, '오천 원' 과 '육천 원' 은 글자가 비슷하다
(유사도 0.83). 그래서 글이 아니라 수량(값 + 단위)을 뽑아 값으로 견준다.

- quantities("가격은 오천 원입니다")           단위가 붙은 수만 수량이다
- quantity_conflicts(a, b)                    같은 단위인데 값이 하나도 겹치지 않는 것
- shared_quantities(a, b)                     양쪽에 같은 값·같은 단위로 있는 것
- canonical_text("오천 원") == canonical_text("5,000원") == "5000원"    견주기용 글
- mentions("가격은 오천 원", "5,000원")       글이 그 값을 담았는가

보수적으로 뽑는다: 잘못 읽은 값은 화면과 말이 어긋난다는 거짓 경고가 되므로, 놓치는 쪽이 낫다.
숫자로 적은 수('5분')는 그대로 믿는다. 한글로 적은 수는 낱말과 겹치므로 아래를 본다
(규칙마다의 근거는 오픈 말뭉치의 대본에서 잰 것이다: docs/research/theme-series.md §8.7).
1. 한 글자 한자어 수(일·삼…구)에 한 글자 단위를 붙여 쓴 꼴은 낱말일 때가 많다. 원·도·세 앞(사원·일도·오세요),
   고유어 수로 세는 단위 앞(사장·사명), 낱말 목록(사회·구분·사위·오일)은 읽지 않는다. 날짜·층·해는 읽는다
   (삼월·사일·오년·삼층: 받아 적은 글이 이렇게 붙여 적는다).
2. 수와 단위는 짝이 맞아야 한다. 고유어 수는 한자어 수로만 세는 단위를 받지 않고 (한 일·세일·두세요·두 분),
   한 글자 한자어 수는 고유어 수로 세는 단위를 받지 않는다 (사 달라고·오시는).
3. 한글로 적은 영(영·공)은 소수일 때만 수다: 공원·공장·영장.
4. 혼자 선 '이' 는 대개 가리키는 말이다. 재는 단위 앞(이 미터)이거나 뒤에 기간을 말하는 낱말이 올 때만 수다 (이 년간).
5. '십일' 은 11 이기도 하고 10일이기도 하다. 일(날)로 나눠 읽는 것은 띄어 썼거나, 조사가 붙었거나,
   뒤에 기간을 말하는 낱말이 올 때만 한다 (십일까지·삼십일 동안. '십일 대 팔'·'이십일 세기' 는 아니다).
6. 시는 24, 월은 12 까지다 (이천시). 붙여 쓴 '두시' 는 뒤가 때를 말하는 조사일 때만 읽는다 (두시면·두시고는 '두다' 다).
   '분의' 뒤에 수가 오면 분수다 (4분의 1).
7. 그래도 남는 흔한 낱말은 목록으로 뺀다: 한층·한쪽·천장·만세·만주·백사장. 뒤에 기간을 말하는 낱말이 오면 읽는다
   (십분 활용 / 십분 사이에).
어림수('이삼천 원'·'서너 개')와 전화번호 같은 숫자열은 값 하나로 읽을 수 없어 뽑지 않는다 (사람이 볼 것으로 남는다).
자리(start·end)는 NFKC 로 맞춘 글에서의 자리다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from frame.domain.script.korean_numbers import (
    SINO_DIGIT_WORDS,
    STANDALONE_NATIVE,
    ZERO_WORDS,
    NumberAt,
    NumberKind,
    is_hangul,
    numbers_at,
)


class Counting(StrEnum):
    """단위를 어떤 수로 세는가."""

    SINO = "SINO"  # 한자어 수로만: 삼 분 · 오천 원 ('세 분' 은 사람이다)
    NATIVE = "NATIVE"  # 작은 수는 고유어 수로: 세 개 · 한 시 (큰 수는 한자어 수도 쓴다: 삼십 명)
    BOTH = "BOTH"  # 둘 다: 한 번 · 일 번, 두 배 · 삼 배


@dataclass(frozen=True)
class Unit:
    name: str  # 적는 꼴 (대표)
    aliases: tuple[str, ...] = ()  # 같은 뜻의 다른 표기
    counts: Counting = Counting.BOTH
    measure: bool = False  # 재는 단위. 가리키는 말 '이' 뒤에 오지 않는다 ('이 미터' 는 2m 다)
    limit: int | None = None  # 값의 상한 (시 24 · 월 12). 넘으면 다른 낱말이다


_S, _N = Counting.SINO, Counting.NATIVE
UNITS: tuple[Unit, ...] = (
    Unit("원", counts=_S),
    Unit("달러", counts=_S, measure=True),
    Unit("%", ("퍼센트", "프로"), _S, measure=True),
    Unit("개", counts=_N),
    Unit("잔", counts=_N),
    Unit("병", counts=_N),
    Unit("캔", counts=_N),
    Unit("컵", counts=_N),
    Unit("봉지", counts=_N),
    Unit("팩", counts=_N),
    Unit("박스", ("상자",), _N),
    Unit("세트"),
    Unit("조각", counts=_N),
    Unit("장", counts=_N),
    Unit("권", counts=_N),
    Unit("벌", counts=_N),
    Unit("켤레", counts=_N),
    Unit("명", counts=_N),
    Unit("인분", counts=_S, measure=True),
    Unit("마리", counts=_N),
    Unit("가지", counts=_N),
    Unit("곳", counts=_N),
    Unit("번"),
    Unit("회"),
    Unit("분", counts=_S),
    Unit("초", counts=_S),
    Unit("시간"),
    Unit("시", counts=_N, limit=24),
    Unit("일", counts=_S),
    Unit("주", ("주일",)),
    Unit("개월", measure=True),
    Unit("달", counts=_N),
    Unit("년", counts=_S),
    Unit("월", counts=_S, limit=12),
    Unit("살", counts=_N),
    Unit("세", counts=_S),
    Unit("g", ("그램",), measure=True),
    Unit("kg", ("킬로그램",), measure=True),
    Unit("ml", ("밀리리터",), measure=True),
    Unit("l", ("리터",), measure=True),
    Unit("cm", ("센티미터", "센티"), measure=True),
    Unit("m", ("미터",), measure=True),
    Unit("km", ("킬로미터",), measure=True),
    Unit("kcal", ("칼로리",), measure=True),
    Unit("도", counts=_S),
    Unit("배"),
    Unit("위", counts=_S),
    Unit("등", counts=_S),
    Unit("층"),
    Unit("단계"),
    Unit("쪽", ("페이지",)),
    Unit("점"),
    Unit("학년", measure=True),
    Unit("주년", counts=_S, measure=True),
    Unit("개국"),
)
# 표기 -> 단위. 긴 표기부터 본다 ('시간' 이 '시' 보다, '달러' 가 '달' 보다 먼저)
_ALIASES: tuple[tuple[str, Unit], ...] = tuple(
    sorted(
        ((alias, unit) for unit in UNITS for alias in (unit.name, *unit.aliases)),
        key=lambda pair: -len(pair[0]),
    )
)
# 단위 바로 뒤에 붙을 수 있는 것. 그 밖의 한글이 이어지면 단위가 아니라 다른 낱말이다 ('오 분야', '두 시간표')
_FOLLOWERS = frozenset(
    "이가은는을를에의도만과와랑로입인임였예요짜씩쯤간째당대부까밖뿐나여라면서"
)  # 첫 음절
_PARTICLES = (
    "으로",
    "보다",
    "처럼",
    "마다",
    "어치",
    "정도",
    "동안",
    "꼴",
    "께",
    "경",
    "생",
)  # 통째로 맞아야 한다
_ENDINGS = frozenset("다고")  # '세 개다'·'세 개고': 바로 뒤에서 낱말이 끝나야 한다
# 붙여 쓴 '두시' 뒤에 올 수 있는 것 (때를 말하는 조사). 그 밖은 '두다' 의 높임이다: 두시면 · 두시고 · 두시는
_TIME_FOLLOWERS = frozenset("에부까쯤경가를의반입인이예였")
# 기간을 말하는 낱말: 모호한 수('이 년'·'십일'·'사 주') 뒤에 오면 수량으로 읽는다
_DURATION = re.compile(
    r"(?:간|째)|"
    r" ?(?:동안|사이|전|후|뒤|만에|이내|이상|이하|정도|쯤|연속|내내|가량|남짓|미만)"
    r"(?:에|은|는|도|이|가|의|으로|로|부터|까지|만|을|를)?(?![가-힣])"
)
# 수에 단위가 붙은 꼴이지만 다른 뜻의 낱말. 붙여 쓴 꼴은 수량으로 읽지 않는다
# ('십 분'·'한 시' 처럼 띄어 쓴 것은 수량이다). '만원'·'천원' 은 값을 말하는 것이 보통이라 넣지 않는다.
_WORDS = frozenset(
    {"십분", "한시", "한번", "한층", "한쪽", "천장", "만일", "만세", "만주", "만회", "만점"}
    | {"천주", "백주", "천도", "백도", "백사장", "백일장"}
)
# 한 글자 한자어 수에 단위를 붙여 쓴 꼴의 낱말 (규칙 1)
_DIGIT_WORDS = frozenset(
    {
        "사회",
        "육회",
        "사분",
        "구분",
        "사위",
        "구위",
        "일주",
        "사주",
        "구주",
        "오일",
        "일일",
        "오점",
        "사초",
    }
)
_NO_LONE_DIGIT = frozenset({"원", "도", "세"})  # 사원·구원 · 일도·팔도 · 오세요·사세요
_NUMBER_WORDS = frozenset({"만일"})  # 수의 꼴이지만 낱말이다 (10001 을 '만일' 이라고 적지 않는다)
# 띄어 써도 동사와 겹치는 짝: 뒤에 기간을 말하는 낱말이 올 때만 수량이다 ('사 주면' 은 '사다 + 주다' 다)
_VERB_PAIRS = frozenset({("사", "주")})
_MONTH_WORDS = {"유": 6, "시": 10}  # 유월 · 시월 (읽는 소리가 다르다)
_FRACTION = re.compile(r"의 ?[\d일이삼사오육칠팔구십백천만]")  # '사 분의 일' 의 분은 시간이 아니다


@dataclass(frozen=True)
class Quantity:
    """수량 하나: 값과 단위, 글에서의 자리."""

    value: Decimal
    unit: str
    start: int
    end: int
    raw: str

    @property
    def text(self) -> str:
        """적는 꼴 하나: '5000원', '2.5배', '30%'."""
        return f"{_plain(self.value)}{self.unit}"


@dataclass(frozen=True)
class QuantityConflict:
    """두 글에 같은 단위의 수량이 있는데 값이 하나도 겹치지 않는다."""

    unit: str
    left: tuple[Decimal, ...]
    right: tuple[Decimal, ...]


def quantities(text: str) -> tuple[Quantity, ...]:
    """글에 나온 수량들 (나온 순서). 단위가 붙은 수만 센다."""
    norm = unicodedata.normalize("NFKC", text)
    lower = norm.lower()
    found: list[Quantity] = []
    index = 0
    while index < len(norm):
        quantity = _quantity_at(norm, lower, index) if _can_start(norm, index) else None
        if quantity is None:
            index += 1
        else:
            found.append(quantity)
            index = quantity.end
    return tuple(found)


def shared_quantities(left: str, right: str) -> tuple[Quantity, ...]:
    """left 의 수량 가운데 right 에도 같은 값·같은 단위로 있는 것."""
    there = {(q.value, q.unit) for q in quantities(right)}
    return tuple(q for q in quantities(left) if (q.value, q.unit) in there)


def quantity_conflicts(left: str, right: str) -> tuple[QuantityConflict, ...]:
    """같은 단위의 수량이 양쪽에 있는데 겹치는 값이 하나도 없는 단위들.

    한쪽에만 있는 단위, 값이 하나라도 겹치는 단위는 어긋남이 아니다 (한쪽이 더 많이 말했을 뿐이다).
    """
    a, b = _by_unit(quantities(left)), _by_unit(quantities(right))
    conflicts: list[QuantityConflict] = []
    for unit, values in a.items():
        others = b.get(unit)
        if others and not set(values) & set(others):
            conflicts.append(QuantityConflict(unit, tuple(values), tuple(others)))
    return tuple(conflicts)


def mentions(text: str, value: str) -> bool:
    """글이 그 값을 담았는가. 수량은 값으로('오천 원' = '5,000원'), 나머지는 글자로 견준다."""
    wanted = quantities(value)
    if not wanted:
        needle = canonical_text(value)
        return bool(needle) and needle in canonical_text(text)
    found = {(q.value, q.unit) for q in quantities(text)}
    if not all((q.value, q.unit) in found for q in wanted):
        return False
    rest = unicodedata.normalize("NFKC", value)
    for quantity in reversed(wanted):
        rest = rest[: quantity.start] + rest[quantity.end :]
    remainder = canonical_text(rest)
    return len(remainder) <= 1 or remainder in canonical_text(text)


def canonical_text(text: str) -> str:
    """견주기용 글: 수량을 한 가지 꼴('5000원')로 적고, 글자·숫자만 남기고 소문자로."""
    norm = unicodedata.normalize("NFKC", text)
    parts: list[str] = []
    cursor = 0
    for quantity in quantities(norm):
        parts += [norm[cursor : quantity.start], quantity.text]
        cursor = quantity.end
    parts.append(norm[cursor:])
    return "".join(ch for ch in "".join(parts).lower() if ch.isalnum() or ch == "%")


# ---- 안쪽 ----------------------------------------------------------------------
def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _by_unit(found: tuple[Quantity, ...]) -> dict[str, list[Decimal]]:
    grouped: dict[str, list[Decimal]] = {}
    for quantity in found:
        values = grouped.setdefault(quantity.unit, [])
        if quantity.value not in values:
            values.append(quantity.value)
    return grouped


def _can_start(norm: str, index: int) -> bool:
    ch = norm[index]
    previous = norm[index - 1] if index else ""
    if ch.isdigit():
        return not (previous.isdigit() or previous in (".", ","))
    # 한글로 적은 수는 낱말의 처음에서만 시작한다 ('오늘만' 의 '만', '메뉴는' 뒤의 글자를 수로 읽지 않는다)
    return is_hangul(ch) and not previous.isalnum()


def _quantity_at(norm: str, lower: str, start: int) -> Quantity | None:
    month = _month_word_at(norm, lower, start)
    if month is not None:
        return month
    numbers = numbers_at(norm, start)
    longest = numbers[0].end if numbers else start
    for number in numbers:
        text = norm[start : number.end]
        if text in _NUMBER_WORDS:
            continue
        spaced = number.end < len(norm) and norm[number.end] == " "
        found = _unit_at(lower, number.end + 1 if spaced else number.end)
        if found is None:
            continue
        alias, unit, end = found
        raw = norm[start:end]
        if raw in _WORDS and not _DURATION.match(norm[end:]):
            return None  # 수가 아니라 낱말이다. 더 짧게 끊어 읽지도 않는다 (십분 활용)
        attached = None if spaced else alias
        if _plausible(text, number, unit, attached, norm[end:], split_one=number.end < longest):
            return Quantity(number.value, unit.name, start, end, raw)
    return None


def _month_word_at(norm: str, lower: str, start: int) -> Quantity | None:
    """유월 · 시월: 6월과 10월은 읽는 소리가 달라 수로 읽히지 않는다."""
    value = _MONTH_WORDS.get(norm[start])
    if value is None:
        return None
    at = start + 2 if norm.startswith(" ", start + 1) else start + 1
    if not norm.startswith("월", at) or not _may_follow(lower[at + 1 :]):
        return None
    return Quantity(Decimal(value), "월", start, at + 1, norm[start : at + 1])


def _unit_at(lower: str, at: int) -> tuple[str, Unit, int] | None:
    for alias, unit in _ALIASES:
        if not lower.startswith(alias, at):
            continue
        end = at + len(alias)
        following = lower[end:]
        if alias.isascii():
            allowed = not (following[:1].isascii() and following[:1].isalnum())
        else:
            allowed = _may_follow(following)
        # 가장 긴 표기가 글자로는 맞는데 뒤가 이어지면 다른 낱말이다. 더 짧은 표기로 물러서지 않는다
        return (alias, unit, end) if allowed else None
    return None


def _may_follow(following: str) -> bool:
    """단위 뒤에 올 수 있는 것인가: 낱말의 끝, 조사·접미사, 또는 '다'·'고' 로 끝나는 말."""
    first = following[:1]
    if not is_hangul(first) or first in _FOLLOWERS or following.startswith(_PARTICLES):
        return True
    return first in _ENDINGS and not is_hangul(following[1:2])


def _plausible(
    text: str,
    number: NumberAt,
    unit: Unit,
    attached: str | None,
    following: str,
    *,
    split_one: bool,
) -> bool:
    """이 수와 단위가 수량으로 읽힐 만한가 (모듈 설명의 규칙 1~6).

    attached 는 수에 붙여 쓴 단위의 표기다 (띄어 썼으면 None). split_one 은 더 긴 수가 있었는데 그 끝을 떼어
    단위로 읽는 경우다 ('십일' 을 '십' + '일').
    """
    value = number.value
    whole = value == value.to_integral_value()
    if unit.limit is not None and not (whole and 0 < value <= unit.limit):
        return False
    if unit.name == "분" and _FRACTION.match(following):
        return False  # 4분의 1
    if number.kind is NumberKind.DIGIT:
        return True  # 숫자로 적은 수는 모호하지 않다
    if text[0] in ZERO_WORDS and whole:
        return False  # 공원 · 공장 · 영장
    duration = _DURATION.match(following) is not None
    if text == "이":
        return unit.measure or duration  # 이 미터 · 이 년간 (이 분 · 이 일은 가리키는 말이다)
    if number.kind is NumberKind.NATIVE:
        if unit.counts is Counting.SINO or text.endswith(STANDALONE_NATIVE):
            return False  # 한 일 · 세일 · 두세요 · 하나도
    elif text in SINO_DIGIT_WORDS and unit.counts is Counting.NATIVE:
        return False  # 사 달라고 · 오시는 · 사명
    last = text.rsplit(" ", 1)[-1]  # 수의 마지막 낱말 ('백 사' 의 '사')
    if attached is not None and len(last) == 1 and len(attached) == 1:
        if last in SINO_DIGIT_WORDS and (
            unit.counts is Counting.NATIVE
            or unit.name in _NO_LONE_DIGIT
            or last + attached in _DIGIT_WORDS
        ):
            return False  # 사장 · 백 사장 / 사원 · 일도 · 오세요 / 사회 · 구분 · 오일
        first = following[:1]
        if unit.name == "시" and is_hangul(first) and first not in _TIME_FOLLOWERS:
            return False  # 두시면 · 두시고
    if unit.name == "일" and attached is not None and split_one and following[:1] in ("", " "):
        return duration  # 십일 대 팔 · 이십일 세기 는 수다. 삼십일 동안 은 30일이다
    if (text, unit.name) in _VERB_PAIRS:
        return duration  # 사 주면 (사 주 동안 은 4주다)
    return True
