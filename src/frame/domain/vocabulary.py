"""요구조건 술어(predicate) 어휘와 값 정규화.

왜 필요한가
- 'color' / 'colour' / '색상' 이 서로 다른 조건이 되면 요구조건 키·검수·수정 전파가 모두 어긋난다.
- 검수기는 술어마다 다른 비교(색 거리, 문구 일치, 위치 영역)를 써야 하므로 값 종류(ValueKind)가 필요하다.
- 관계 술어(holds, wears …)는 상대 객체가 필수이고, 속성 술어는 상대 객체가 있으면 안 된다.
- 'white_shirt' / 'White shirt' 처럼 같은 뜻의 값이 다르게 저장되면 수정 전파(current_value)와 검수가 어긋난다.

규칙
- 술어 정규화: 앞뒤 공백 제거, 소문자, 공백/하이픈 -> '_', 별칭 -> 대표 이름. 미등록 술어도 정규화 후 허용(확장성).
- 값 정규화는 값 종류별로 다르다(normalize_value). LITERAL(화면 문구·브랜드)은 철자를 그대로 보존한다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from .errors import ValidationError


class ValueKind(StrEnum):
    COLOR = "COLOR"  # 색 이름/HEX -> 색 거리 비교. 소문자, 밑줄 -> 공백
    TEXT = "TEXT"  # 서술형 값 -> 의미 일치 판정. 소문자, 공백 정리
    LITERAL = "LITERAL"  # 화면 문구·브랜드 -> 철자 그대로 (앞뒤 공백만 제거)
    NUMBER = "NUMBER"  # 수량 -> 수치 비교. "03" -> "3"
    POSITION = "POSITION"  # 화면 내 위치 -> top/bottom/left/right/center/foreground/background 토큰
    ENTITY = "ENTITY"  # 관계 상대 객체 키 (그대로)


@dataclass(frozen=True)
class PredicateSpec:
    name: str
    value_kind: ValueKind
    relational: bool = False
    aliases: tuple[str, ...] = ()
    description: str = ""


BUILTIN_PREDICATES: tuple[PredicateSpec, ...] = (
    # --- 객체 속성 ---
    PredicateSpec(
        "color", ValueKind.COLOR, aliases=("colour", "색상", "색", "컬러"), description="객체의 색"
    ),
    PredicateSpec("texture", ValueKind.TEXT, aliases=("질감",), description="표면 질감"),
    PredicateSpec("material", ValueKind.TEXT, aliases=("재질", "소재"), description="재질"),
    PredicateSpec("shape", ValueKind.TEXT, aliases=("형태", "모양"), description="형태"),
    PredicateSpec("size", ValueKind.TEXT, aliases=("크기",), description="크기"),
    PredicateSpec("position", ValueKind.POSITION, aliases=("위치",), description="화면 내 위치"),
    PredicateSpec(
        "text",
        ValueKind.LITERAL,
        aliases=("문구", "텍스트", "자막", "글자"),
        description="화면에 보이는 문구",
    ),
    PredicateSpec("brand", ValueKind.LITERAL, aliases=("브랜드",), description="브랜드명"),
    PredicateSpec(
        "action", ValueKind.TEXT, aliases=("행동", "동작"), description="객체의 행동·움직임"
    ),
    PredicateSpec("pose", ValueKind.TEXT, aliases=("포즈", "자세"), description="자세"),
    PredicateSpec("expression", ValueKind.TEXT, aliases=("표정",), description="인물 표정"),
    PredicateSpec("outfit", ValueKind.TEXT, aliases=("의상", "옷"), description="의상"),
    PredicateSpec("hair", ValueKind.TEXT, aliases=("머리", "헤어"), description="머리 모양·색"),
    PredicateSpec("age", ValueKind.TEXT, aliases=("나이", "연령"), description="연령대"),
    PredicateSpec("gender", ValueKind.TEXT, aliases=("성별",), description="성별"),
    PredicateSpec("style", ValueKind.TEXT, aliases=("스타일", "화풍"), description="시각 스타일"),
    PredicateSpec("count", ValueKind.NUMBER, aliases=("개수", "수량"), description="개수"),
    # --- 장면 수준 조건 (entity_key=None 권장) ---
    PredicateSpec(
        "location", ValueKind.TEXT, aliases=("장소", "배경 장소"), description="장면의 장소"
    ),
    PredicateSpec("lighting", ValueKind.TEXT, aliases=("조명",), description="장면 조명"),
    PredicateSpec("mood", ValueKind.TEXT, aliases=("분위기",), description="장면 분위기"),
    PredicateSpec("camera", ValueKind.TEXT, aliases=("카메라", "샷"), description="카메라/샷 지시"),
    PredicateSpec("time_of_day", ValueKind.TEXT, aliases=("시간대",), description="시간대"),
    PredicateSpec("weather", ValueKind.TEXT, aliases=("날씨",), description="날씨"),
    # --- 관계 술어 (related_entity_key 필수, expected_value == related_entity_key, 배경 객체 불가) ---
    PredicateSpec(
        "holds", ValueKind.ENTITY, True, ("hold", "holding", "들다"), "상대 객체를 들고 있음"
    ),
    PredicateSpec(
        "wears", ValueKind.ENTITY, True, ("wear", "wearing", "입다", "착용"), "상대 객체를 착용"
    ),
    PredicateSpec(
        "near", ValueKind.ENTITY, True, ("beside", "next_to", "옆에"), "상대 객체 옆에 있음"
    ),
    PredicateSpec(
        "looks_at", ValueKind.ENTITY, True, ("look_at", "바라보다"), "상대 객체를 바라봄"
    ),
    PredicateSpec("on", ValueKind.ENTITY, True, ("on_top_of", "위에"), "상대 객체 위에 있음"),
)

_SEPARATORS = re.compile(r"[\s\-]+")
_NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?")
_POSITION_TOKENS: dict[str, tuple[str, ...]] = {
    "top": ("top", "upper", "위", "위쪽", "상단"),
    "bottom": ("bottom", "lower", "아래", "아래쪽", "하단"),
    "left": ("left", "왼쪽", "좌측", "좌"),
    "right": ("right", "오른쪽", "우측", "우"),
    "center": ("center", "centre", "middle", "가운데", "중앙", "중간"),
    "foreground": ("foreground", "front", "앞", "앞쪽", "전경"),
    "background": ("background", "back", "뒤", "뒤쪽", "후경"),
}
_POSITION_LOOKUP = {
    alias: canon for canon, aliases in _POSITION_TOKENS.items() for alias in aliases
}


def canonical_form(raw: str) -> str:
    return _SEPARATORS.sub("_", (raw or "").strip().lower())


def _collapse(raw: str) -> str:
    return " ".join(raw.replace("_", " ").split())


def _normalize_number(raw: str) -> str:
    match = _NUMBER.match(raw.strip().replace(",", ""))
    if match is None:
        return _collapse(raw).lower()
    value = float(match.group(0))
    return str(int(value)) if value.is_integer() else repr(value)


def _normalize_position(raw: str) -> str:
    tokens = _collapse(raw.replace("-", " ")).lower().split()
    mapped = [_POSITION_LOOKUP.get(token, token) for token in tokens]
    ordered = [canon for canon in _POSITION_TOKENS if canon in mapped]
    extras = [token for token in mapped if token not in _POSITION_TOKENS]
    return " ".join(ordered + extras)


class PredicateVocabulary:
    def __init__(self, specs: Iterable[PredicateSpec] = BUILTIN_PREDICATES) -> None:
        self._specs: dict[str, PredicateSpec] = {}
        self._aliases: dict[str, str] = {}
        for spec in specs:
            self._specs[spec.name] = spec
            self._aliases[canonical_form(spec.name)] = spec.name
            for alias in spec.aliases:
                self._aliases[canonical_form(alias)] = spec.name

    def normalize(self, raw: str) -> str:
        form = canonical_form(raw)
        if not form:
            raise ValidationError("술어(predicate)가 비어 있습니다")
        return self._aliases.get(form, form)

    def normalize_value(self, predicate: str, value: str) -> str:
        """값 종류별 정규화. 미등록 술어는 TEXT 규칙을 따른다."""
        kind = self.value_kind(predicate) or ValueKind.TEXT
        raw = (value or "").strip()
        if kind is ValueKind.LITERAL or kind is ValueKind.ENTITY:
            return raw
        if kind is ValueKind.NUMBER:
            return _normalize_number(raw)
        if kind is ValueKind.POSITION:
            return _normalize_position(raw)
        return _collapse(raw).lower()  # COLOR, TEXT (소문자화는 ASCII 에만 영향)

    def spec(self, name: str) -> PredicateSpec | None:
        return self._specs.get(self.normalize(name))

    def is_relational(self, name: str) -> bool | None:
        """True/False, 미등록 술어면 None (판정 보류)."""
        spec = self.spec(name)
        return None if spec is None else spec.relational

    def value_kind(self, name: str) -> ValueKind | None:
        spec = self.spec(name)
        return None if spec is None else spec.value_kind

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def specs(self) -> tuple[PredicateSpec, ...]:
        return tuple(self._specs.values())

    def with_predicates(self, *specs: PredicateSpec) -> PredicateVocabulary:
        """기본 어휘를 건드리지 않고 프로젝트별 술어를 덧붙인 새 어휘를 만든다."""
        return PredicateVocabulary((*self._specs.values(), *specs))


DEFAULT_VOCABULARY = PredicateVocabulary()
