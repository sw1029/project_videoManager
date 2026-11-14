"""값 비교: 골드 값과 모델 값이 같은 조건을 뜻하는가 (요구조건 F1 의 '값이 맞는가').

어휘(vocabulary)의 값 정규화를 거친 뒤 값 종류별로 견준다. 같은 패키지의 짝 맞추기(_matching)도
여기의 낱말 쪼개기(words)와 어간(stem)을 쓴다.
"""

from __future__ import annotations

import re

from frame.domain.vocabulary import DEFAULT_VOCABULARY, ValueKind

_WORD = re.compile(r"[^\W_]+")
_NUMERIC = re.compile(r"[+-]?\d+(?:\.\d+)?")
_LEADING_DIGITS = re.compile(r"\d+")
_QUOTES = "'\"‘’“”「」『』`"
_STOPWORDS = frozenset(
    "a an the and or of with to at in on into onto for from by while its his her their is are s".split()
)  # s: 소유격(bird's)이 쪼개져 남은 것
_VOWELS = frozenset("aeiouy")
# (어미, 그 어미를 걷어낼 최소 낱말 길이). 앞에서부터 하나만 걷어낸다
_SUFFIXES = (("ing", 6), ("ied", 5), ("ies", 5), ("es", 5), ("ed", 5), ("ly", 6), ("s", 4))
_COLOR_WORDS = {
    "grey": "gray",
    "빨간": "red", "빨강": "red", "빨간색": "red", "빨강색": "red", "붉은": "red",
    "주황": "orange", "주황색": "orange",
    "노란": "yellow", "노랑": "yellow", "노란색": "yellow", "노랑색": "yellow",
    "초록": "green", "초록색": "green", "녹색": "green",
    "청록": "teal", "청록색": "teal",
    "파란": "blue", "파랑": "blue", "파란색": "blue", "파랑색": "blue",
    "남색": "navy",
    "보라": "purple", "보라색": "purple",
    "분홍": "pink", "분홍색": "pink", "핑크": "pink", "핑크색": "pink",
    "갈색": "brown",
    "흰": "white", "하얀": "white", "흰색": "white", "하얀색": "white",
    "검은": "black", "검정": "black", "검은색": "black", "검정색": "black",
    "회색": "gray",
    "은색": "silver",
    "금색": "gold",
}  # fmt: skip
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "하나": "1", "한": "1", "둘": "2", "두": "2", "셋": "3", "세": "3", "넷": "4", "네": "4",
    "다섯": "5", "여섯": "6", "일곱": "7", "여덟": "8", "아홉": "9", "열": "10",
}  # fmt: skip


def values_match(predicate: str, expected: str, actual: str) -> bool:
    """골드 값과 모델 값이 같은 조건을 뜻하는가. 어휘의 값 정규화를 거친 뒤 값 종류별로 견준다.

    - ENTITY(관계): 상대 객체를 맞춘 것으로 비교가 끝났다
    - LITERAL(문구·브랜드): 따옴표·공백만 정리하고 철자·대소문자까지 같아야 한다
    - NUMBER: 숫자로 (three / 세 마리 → 3)
    - 그 밖(COLOR·TEXT·POSITION): 골드 값의 내용어가 모델 값에 모두 있으면 맞춤 (warm ⊂ warm cafe lighting).
      영어 어미(smiling/smiles → smile), 한국어 색 이름(빨간색 → red), 붙여 쓰기(close up / closeup)를 맞춘다
    """
    predicate = DEFAULT_VOCABULARY.normalize(predicate)
    kind = DEFAULT_VOCABULARY.value_kind(predicate)
    if kind is ValueKind.ENTITY:
        return True
    expected = DEFAULT_VOCABULARY.normalize_value(predicate, expected)
    actual = DEFAULT_VOCABULARY.normalize_value(predicate, actual)
    if kind is ValueKind.LITERAL:
        return _literal(expected) == _literal(actual)
    if kind is ValueKind.NUMBER:
        return _number(expected) == _number(actual)
    return _phrase_match(expected, actual)


def _phrase_match(expected: str, actual: str) -> bool:
    """골드 값의 내용어가 모델 값에 모두 있는가. 띄어 쓴 말과 붙여 쓴 말(close up / closeup)은 같다고 본다."""
    wanted = [w for w in words(expected) if w not in _STOPWORDS]
    found = words(actual)
    if not wanted:
        return "".join(words(expected)) == "".join(found)
    stems = {stem(w) for w in found if w not in _STOPWORDS}
    joined = {
        "".join(found[i:j])
        for i in range(len(found))
        for j in range(i + 1, min(i + 4, len(found) + 1))
    }  # 모델 값의 이웃한 1~3 낱말을 붙인 것
    i = 0
    while i < len(wanted):
        if stem(wanted[i]) in stems or wanted[i] in joined:
            i += 1
            continue
        size = next(
            (n for n in (2, 3) if i + n <= len(wanted) and "".join(wanted[i : i + n]) in joined),
            0,
        )
        if not size:
            return False
        i += size
    return True


def words(text: str) -> list[str]:
    """소문자 낱말 목록. 한국어 색 이름은 영어로 바꾼다 (빨간색 → red)."""
    return [_COLOR_WORDS.get(w, w) for w in _WORD.findall(text.lower())]


def stem(word: str) -> str:
    """영어 낱말의 굴절 어미를 걷어낸다 (smiling/smiles/smile → smil, rainy → rain). 한국어·숫자는 그대로."""
    if not (word.isascii() and word.isalpha()):
        return word
    for suffix, min_length in _SUFFIXES:
        if len(word) >= min_length and word.endswith(suffix) and not word.endswith("ss"):
            base = word[: -len(suffix)]
            if _VOWELS & set(base):
                word = base
                break
    if len(word) > 3 and word.endswith("y"):
        word = word[:-1]
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in _VOWELS:
        word = word[:-1]
    return word


def _literal(value: str) -> str:
    return " ".join(value.strip().strip(_QUOTES).split())


def _number(value: str) -> str:
    if _NUMERIC.fullmatch(value):
        return value
    parts = words(value)
    if not parts:
        return ""
    first = _NUMBER_WORDS.get(parts[0], parts[0])
    digits = _LEADING_DIGITS.match(first)
    return digits.group(0) if digits else first
