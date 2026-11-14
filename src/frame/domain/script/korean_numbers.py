"""한국어의 수 읽기와 수량 (ADR-0028). 순수 함수.

말로는 '오천 원', 화면에는 '5,000원' 이다. 글자로 견주면 다른 글이고, '오천 원' 과 '육천 원' 은 글자가 비슷하다
(유사도 0.83). 그래서 글이 아니라 수량(값 + 단위)을 뽑아 값으로 견준다.

- read_number("5,000") -> "오천"              적은 수 -> 읽는 소리 (한자어 수)
- parse_number("오천") -> 5000                읽는 소리·섞어 적은 수("1만 2천")·고유어 수("스물한") -> 값
- quantities("가격은 오천 원입니다")           단위가 붙은 수만 수량이다
- quantity_conflicts(a, b)                    같은 단위인데 값이 하나도 겹치지 않는 것
- canonical_text("오천 원") == canonical_text("5,000원") == "5000원"    견주기용 글
- mentions("가격은 오천 원", "5,000원")       글이 그 값을 담았는가

보수적으로 뽑는다. 단위가 붙지 않은 수는 수량이 아니다: '이 메뉴' 의 '이', '일을 한다' 의 '일', '오늘' 의 '오' 를
수로 읽지 않는다. 혼자 선 '이' 는 단위 앞에서도 수로 읽지 않는다 ('이 잔' 은 대개 '이 컵' 이라는 뜻이다).
자리(start·end)는 NFKC 로 맞춘 글에서의 자리다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

_SINO = ("", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구")
_SMALL_UNITS = ("", "십", "백", "천")
_BIG_UNITS = ("", "만", "억", "조")

_SINO_DIGITS = {
    "영": 0,
    "공": 0,
    "일": 1,
    "이": 2,
    "삼": 3,
    "사": 4,
    "오": 5,
    "육": 6,
    "륙": 6,
    "칠": 7,
    "팔": 8,
    "구": 9,
}
_SMALL = {"십": 10, "백": 100, "천": 1000}
_BIG = {"만": 10**4, "억": 10**8, "조": 10**12}
# 고유어 수: 열 단위 + 한 자리. 단위 앞의 꼴(한·두·세·네·스무)도 받는다
_NATIVE_TENS = {
    "스물": 20,
    "스무": 20,
    "서른": 30,
    "마흔": 40,
    "예순": 60,
    "일흔": 70,
    "여든": 80,
    "아흔": 90,
    "열": 10,
    "쉰": 50,
}
_NATIVE_ONES = {
    "하나": 1,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
    "한": 1,
    "둘": 2,
    "두": 2,
    "셋": 3,
    "세": 3,
    "석": 3,
    "넷": 4,
    "네": 4,
    "넉": 4,
}
_DIGIT_RUN = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")  # 쉼표는 세 자리 구분으로만 받는다

# 단위: 적는 꼴 하나로 모은다 (앞이 대표, 뒤가 같은 뜻의 다른 표기)
UNITS: dict[str, tuple[str, ...]] = {
    "원": ("원",),
    "%": ("%", "퍼센트", "프로"),
    "개": ("개",),
    "잔": ("잔",),
    "병": ("병",),
    "캔": ("캔",),
    "컵": ("컵",),
    "봉지": ("봉지",),
    "팩": ("팩",),
    "박스": ("박스", "상자"),
    "세트": ("세트",),
    "조각": ("조각",),
    "장": ("장",),
    "권": ("권",),
    "벌": ("벌",),
    "켤레": ("켤레",),
    "명": ("명",),
    "인분": ("인분",),
    "마리": ("마리",),
    "가지": ("가지",),
    "곳": ("곳",),
    "번": ("번",),
    "회": ("회",),
    "분": ("분",),
    "초": ("초",),
    "시간": ("시간",),
    "시": ("시",),
    "일": ("일",),
    "주": ("주일", "주"),
    "개월": ("개월",),
    "달": ("달",),
    "년": ("년",),
    "살": ("살",),
    "세": ("세",),
    "g": ("g", "그램"),
    "kg": ("kg", "킬로그램"),
    "ml": ("ml", "밀리리터"),
    "l": ("l", "리터"),
    "cm": ("cm", "센티미터", "센티"),
    "m": ("m", "미터"),
    "km": ("km", "킬로미터"),
    "kcal": ("kcal", "칼로리"),
    "도": ("도",),
    "배": ("배",),
    "위": ("위",),
    "등": ("등",),
    "층": ("층",),
    "단계": ("단계",),
    "쪽": ("쪽", "페이지"),
}
_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((alias, unit) for unit, aliases in UNITS.items() for alias in aliases),
        key=lambda pair: -len(pair[0]),
    )
)
# 단위 바로 뒤에 붙을 수 있는 음절 (조사·접미사의 첫 음절). 그 밖의 한글이 이어지면 단위가 아니라 다른 낱말이다
# ('오 분야', '두 시간표', '세 세트' 의 '세트').
_FOLLOWERS = frozenset("이가은는을를에의도만과와랑로입인임였예요짜씩쯤간째당대부까밖뿐나여라면서")


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


def read_number(digits: str) -> str:
    """숫자를 읽는 소리로: '5,000' -> '오천', '4500' -> '사천오백', '10' -> '십'. 한자어 수로만 읽는다."""
    value = int(digits.replace(",", "") or 0)
    if value == 0:
        return "영"
    groups: list[str] = []
    index = 0
    while value > 0 and index < len(_BIG_UNITS):
        value, chunk = divmod(value, 10_000)
        if chunk:
            text = ""
            for position in range(3, -1, -1):
                digit = chunk // 10**position % 10
                if digit == 0:
                    continue
                # 십·백·천 앞의 '일' 은 읽지 않는다 (만 단위 이상의 묶음에서도 같다)
                text += ("" if digit == 1 and position > 0 else _SINO[digit]) + _SMALL_UNITS[
                    position
                ]
            # '일만' 은 '만' 으로 읽는다
            groups.append(("" if text == "일" and index == 1 else text) + _BIG_UNITS[index])
        index += 1
    return "".join(reversed(groups))


def parse_number(text: str) -> Decimal | None:
    """글 전체가 수 하나이면 그 값. 아니면 None (단위가 붙은 글·수가 둘인 글도 None)."""
    norm = unicodedata.normalize("NFKC", text).strip()
    if not norm:
        return None
    for end, value in _numbers_at(norm, 0):
        if end == len(norm):
            return value
    return None


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
    return len(remainder) < 2 or remainder in canonical_text(text)


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


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


def _can_start(norm: str, index: int) -> bool:
    ch = norm[index]
    previous = norm[index - 1] if index else ""
    if ch.isdigit():
        return not (previous.isdigit() or previous in (".", ","))
    # 한글로 적은 수는 낱말의 처음에서만 시작한다 ('오늘만' 의 '만', '메뉴는' 뒤의 글자를 수로 읽지 않는다)
    return _is_hangul(ch) and not previous.isalnum()


def _quantity_at(norm: str, lower: str, start: int) -> Quantity | None:
    for end, value in _numbers_at(norm, start):
        if norm[start:end] == "이":
            continue  # 혼자 선 '이' 는 가리키는 말이다
        at = end + 1 if end < len(norm) and norm[end] == " " else end
        unit = _unit_at(lower, at)
        if unit is not None:
            name, unit_end = unit
            return Quantity(value, name, start, unit_end, norm[start:unit_end])
    return None


def _unit_at(lower: str, at: int) -> tuple[str, int] | None:
    for alias, unit in _ALIASES:
        if not lower.startswith(alias, at):
            continue
        end = at + len(alias)
        following = lower[end] if end < len(lower) else ""
        if alias.isascii():
            allowed = not (following.isascii() and following.isalnum())
        else:
            allowed = not _is_hangul(following) or following in _FOLLOWERS
        # 가장 긴 표기가 글자로는 맞는데 뒤가 이어지면 다른 낱말이다. 더 짧은 표기로 물러서지 않는다
        return (unit, end) if allowed else None
    return None


def _numbers_at(norm: str, start: int) -> list[tuple[int, Decimal]]:
    """start 에서 시작하는 수의 후보 (끝 자리, 값). 긴 것부터."""
    found = {**dict(_scan_native(norm, start)), **dict(_scan_mixed(norm, start))}
    return sorted(found.items(), key=lambda item: -item[0])


def _scan_native(norm: str, start: int) -> list[tuple[int, Decimal]]:
    found: list[tuple[int, Decimal]] = []
    index, value = start, 0
    for word, tens in _NATIVE_TENS.items():
        if norm.startswith(word, index):
            index, value = index + len(word), tens
            found.append((index, Decimal(value)))
            break
    for word, ones in _NATIVE_ONES.items():
        if norm.startswith(word, index):
            found.append((index + len(word), Decimal(value + ones)))
            break
    return found


def _scan_mixed(norm: str, start: int) -> list[tuple[int, Decimal]]:
    """숫자·한자어 수·섞어 적은 수 ('5,000', '오천', '1만 2천', '이 점 오')."""
    found: list[tuple[int, Decimal]] = []
    total = Decimal(0)  # 만·억·조 로 닫힌 값
    section = Decimal(0)  # 지금 만 단위 묶음의 값
    pending: Decimal | None = None  # 십·백·천·만 을 기다리는 수
    last_small = 10_000  # 묶음 안에서 십·백·천 은 큰 것부터 나온다
    last_big: int | None = None  # 만·억·조 도 큰 것부터 나온다
    index = start
    while index < len(norm):
        at = index
        if found and norm[at] == " " and at + 1 < len(norm):
            at += 1  # 수 안의 띄어쓰기 하나 ('만 이천')
        ch = norm[at]
        run = _DIGIT_RUN.match(norm, at) if ch.isdigit() else None
        if run is not None:
            if pending is not None:
                break
            pending = Decimal(run.group(0).replace(",", ""))
            index = run.end()
        elif ch in _SINO_DIGITS:
            if pending is not None:
                break
            pending = Decimal(_SINO_DIGITS[ch])
            index = at + 1
        elif ch in _SMALL:
            unit = _SMALL[ch]
            if unit >= last_small or (pending is not None and pending not in range(1, 10)):
                break
            section += (pending if pending is not None else 1) * unit
            pending, last_small, index = None, unit, at + 1
        elif ch in _BIG:
            big = _BIG[ch]
            if last_big is not None and big >= last_big:
                break
            group = section + (pending or 0)
            if group != group.to_integral_value() and section:
                break
            total += (group if group else Decimal(1)) * big  # '만 원' 의 '만' 은 1만
            section, pending, last_small, last_big, index = Decimal(0), None, 10_000, big, at + 1
        elif ch == "점" and found:
            fraction_at = at + 2 if at + 1 < len(norm) and norm[at + 1] == " " else at + 1
            digits = ""
            while fraction_at < len(norm) and norm[fraction_at] in _SINO_DIGITS:
                digits += str(_SINO_DIGITS[norm[fraction_at]])
                fraction_at += 1
            whole = total + section + (pending or 0)
            if digits and whole == whole.to_integral_value():
                found.append((fraction_at, whole + Decimal(f"0.{digits}")))
            break
        else:
            break
        found.append((index, total + section + (pending or 0)))
    return found
