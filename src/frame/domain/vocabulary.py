"""요구조건 술어(predicate) 어휘.

왜 필요한가
- 'color' / 'colour' / '색상' 이 서로 다른 조건이 되면 요구조건 키·검수·수정 전파가 모두 어긋난다.
- 검수기는 술어마다 다른 비교(색 거리, 문구 일치, 위치 영역)를 써야 하므로 값 종류(ValueKind)가 필요하다.
- 관계 술어(holds, wears …)는 상대 객체가 필수이고, 속성 술어는 상대 객체가 있으면 안 된다.

규칙
- 정규화: 앞뒤 공백 제거, 소문자, 공백/하이픈 -> '_', 별칭 -> 대표 이름.
- 등록되지 않은 술어도 정규화한 형태로 허용한다(확장성). 그 경우 spec() 은 None 이고 관계 여부는 '판정 보류' 다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from .errors import ValidationError


class ValueKind(StrEnum):
    COLOR = "COLOR"  # 색 이름/HEX -> 색 거리 비교
    TEXT = "TEXT"  # 자유 문구 -> 의미 일치 판정
    NUMBER = "NUMBER"  # 수량 -> 수치 비교
    POSITION = "POSITION"  # 화면 내 위치 -> 영역 비교
    ENTITY = "ENTITY"  # 관계 상대 객체 키


@dataclass(frozen=True)
class PredicateSpec:
    name: str
    value_kind: ValueKind
    relational: bool = False
    aliases: tuple[str, ...] = ()
    description: str = ""


BUILTIN_PREDICATES: tuple[PredicateSpec, ...] = (
    # --- 속성 술어 ---
    PredicateSpec(
        "color", ValueKind.COLOR, aliases=("colour", "색상", "색", "컬러"), description="객체의 색"
    ),
    PredicateSpec("texture", ValueKind.TEXT, aliases=("질감",), description="표면 질감"),
    PredicateSpec("material", ValueKind.TEXT, aliases=("재질", "소재"), description="재질"),
    PredicateSpec("shape", ValueKind.TEXT, aliases=("형태", "모양"), description="형태"),
    PredicateSpec("size", ValueKind.TEXT, aliases=("크기",), description="크기"),
    PredicateSpec("position", ValueKind.POSITION, aliases=("위치",), description="화면 내 위치"),
    PredicateSpec(
        "text", ValueKind.TEXT, aliases=("문구", "텍스트", "자막"), description="문구 내용"
    ),
    PredicateSpec("action", ValueKind.TEXT, aliases=("행동", "동작"), description="객체의 행동"),
    PredicateSpec("expression", ValueKind.TEXT, aliases=("표정",), description="인물 표정"),
    PredicateSpec("outfit", ValueKind.TEXT, aliases=("의상", "옷"), description="의상"),
    PredicateSpec("lighting", ValueKind.TEXT, aliases=("조명",), description="장면 조명"),
    PredicateSpec("mood", ValueKind.TEXT, aliases=("분위기",), description="장면 분위기"),
    PredicateSpec("camera", ValueKind.TEXT, aliases=("카메라", "샷"), description="카메라/샷 지시"),
    PredicateSpec("count", ValueKind.NUMBER, aliases=("개수", "수량"), description="개수"),
    # --- 관계 술어 (related_entity_key 필수, expected_value == related_entity_key) ---
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


def canonical_form(raw: str) -> str:
    return _SEPARATORS.sub("_", (raw or "").strip().lower())


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
