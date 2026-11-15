"""한국어의 수 읽기 (ADR-0028). 순수 함수. 수에 단위가 붙은 '수량' 은 quantities.py 가 맡는다.

- read_number("5,000") -> "오천"   적은 수 -> 읽는 소리 (한자어 수)
- parse_number("오천") -> 5000     글 전체가 수 하나이면 그 값
- numbers_at(글, 자리)              그 자리에서 시작하는 수의 후보들 (긴 것부터). 수량을 뽑을 때 쓴다

받는 꼴
- 숫자: 5,000 · 4500 · 2.5 (쉼표는 세 자리 구분으로만 받는다)
- 한자어 수: 오천 · 사천오백 · 만 이천. 낱말마다 띄어 적은 것도 수 하나다 (이천 십 이 = 2012)
- 섞어 적은 수: 3천 · 1만 2천 · 2억 5천만
- 고유어 수: 한 · 두 · 열두 · 스물한 · 마흔 여섯
- 소수: 이 점 오 · 일 쩜 오 · 오 쩜 구 사 (점 뒤는 한 자리씩 읽는다. '쩜' 은 말할 때의 소수점이다)
받지 않는 꼴
- 혼자 선 '억'·'조': '만'·'천' 은 혼자서도 수이지만 '억'·'조' 는 앞에 수가 있어야 한다 (조명·조회·조개는 낱말이다)
- 어림수('이삼천'·'서너')와 숫자열(전화번호): 값 하나로 읽을 수 없다
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

_SINO = ("", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구")
_SMALL_UNITS = ("", "십", "백", "천")
_BIG_UNITS = ("", "만", "억", "조")

ZERO_WORDS = frozenset("영공")  # 한글로 적은 영
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
# 한 글자 한자어 수 가운데 1~9. '이' 는 가리키는 말과 겹쳐 따로 다룬다 (quantities.py)
SINO_DIGIT_WORDS = frozenset(word for word, value in _SINO_DIGITS.items() if value)
_SMALL = {"십": 10, "백": 100, "천": 1000}
_BIG = {"만": 10**4, "억": 10**8, "조": 10**12}
_BARE_BIG = 10**4  # 이보다 큰 묶음(억·조)은 혼자 서지 못한다
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
# 단위 앞에 서지 못하는 꼴: 단위 앞에서는 한·두·세·네 로 바뀐다 ('하나 개' 라고 하지 않는다)
STANDALONE_NATIVE = ("하나", "둘", "셋", "넷")
_DIGIT_RUN = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")  # 쉼표는 세 자리 구분으로만 받는다
_POINTS = frozenset("점쩜")


class NumberKind(StrEnum):
    DIGIT = "DIGIT"  # 숫자가 든 수 (5,000 · 3천). 모호하지 않다
    SINO = "SINO"  # 한글로 적은 한자어 수 (오천)
    NATIVE = "NATIVE"  # 고유어 수 (한 · 스물한)


@dataclass(frozen=True)
class NumberAt:
    """글의 한 자리에서 시작하는 수의 후보."""

    end: int
    value: Decimal
    kind: NumberKind


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
    for number in numbers_at(norm, 0):
        if number.end == len(norm):
            return number.value
    return None


def numbers_at(norm: str, start: int) -> list[NumberAt]:
    """start 에서 시작하는 수의 후보. 긴 것부터. norm 은 NFKC 로 맞춘 글이다."""
    found = {
        end: NumberAt(end, value, NumberKind.NATIVE) for end, value in _scan_native(norm, start)
    }
    for end, value in _scan_mixed(norm, start):
        digits = any(ch.isdigit() for ch in norm[start:end])
        found[end] = NumberAt(end, value, NumberKind.DIGIT if digits else NumberKind.SINO)
    return sorted(found.values(), key=lambda number: -number.end)


def is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


# ---- 안쪽 ----------------------------------------------------------------------
def _scan_native(norm: str, start: int) -> list[tuple[int, Decimal]]:
    found: list[tuple[int, Decimal]] = []
    index, value = start, 0
    for word, tens in _NATIVE_TENS.items():
        if norm.startswith(word, index):
            index, value = index + len(word), tens
            found.append((index, Decimal(value)))
            break
    # 열 단위와 한 자리를 띄어 적기도 한다 ('마흔 여섯')
    ones_at = index + 1 if value and norm.startswith(" ", index) else index
    for word, ones in _NATIVE_ONES.items():
        if norm.startswith(word, ones_at):
            found.append((ones_at + len(word), Decimal(value + ones)))
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
            if not group and big > _BARE_BIG:
                break  # 혼자 선 '억'·'조' 는 수가 아니다
            total += (group or Decimal(1)) * big  # '만 원' 의 '만' 은 1만
            section, pending, last_small, last_big, index = Decimal(0), None, 10_000, big, at + 1
        elif ch in _POINTS and found:
            whole = total + section + (pending or 0)
            if whole == whole.to_integral_value():
                found += _fractions(norm, at + 1, whole)
            break
        else:
            break
        found.append((index, total + section + (pending or 0)))
    return found


def _fractions(norm: str, start: int, whole: Decimal) -> list[tuple[int, Decimal]]:
    """소수점 뒤를 한 자리씩 읽는다. 자리마다 후보 하나 ('일 점 오 일' 은 1.5일일 수도 있다)."""
    found: list[tuple[int, Decimal]] = []
    digits = ""
    cursor = start
    while True:
        at = cursor + 1 if norm.startswith(" ", cursor) else cursor
        if at >= len(norm) or norm[at] not in _SINO_DIGITS:
            return found
        digits += str(_SINO_DIGITS[norm[at]])
        cursor = at + 1
        found.append((cursor, whole + Decimal(f"0.{digits}")))
