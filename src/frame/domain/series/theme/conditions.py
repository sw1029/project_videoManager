"""테마가 쓰는 조건 한 줄 (술어 = 값). 값은 IR 의 어휘로 정규화한다 (ADR-0025).

고정 출연의 고정 속성, 자리의 조건, 스타일의 조건이 모두 이 타입이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from frame.domain.errors import ValidationError
from frame.domain.vocabulary import DEFAULT_VOCABULARY


@dataclass(frozen=True)
class Condition:
    """술어 = 값. 술어와 값은 IR 의 요구조건과 같은 어휘 규칙으로 정규화된다."""

    predicate: str
    value: str

    def __post_init__(self) -> None:
        predicate = DEFAULT_VOCABULARY.normalize(self.predicate)
        object.__setattr__(self, "predicate", predicate)
        value = DEFAULT_VOCABULARY.normalize_value(predicate, self.value or "")
        if not value:
            raise ValidationError(f"조건 '{predicate}' 의 값이 비어 있습니다")
        object.__setattr__(self, "value", value)

    def to_dict(self) -> dict[str, str]:
        return {"predicate": self.predicate, "value": self.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Condition:
        return cls(str(data["predicate"]), str(data["value"]))


def _conditions(items: Sequence[Mapping[str, Any]] | None) -> tuple[Condition, ...]:
    return tuple(Condition.from_dict(item) for item in items or ())
