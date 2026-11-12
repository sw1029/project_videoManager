"""변경 요청 해석 포트 (US-01, ADR-0019): 사용자가 글로 쓴 수정 요청을 구조화된 변경 하나로 옮긴다.

컨셉 화면의 '변경 요청' 칸: "컵 색상만 청록색으로 동일해줘. 인물과 배경은 유지해줘."
-> (객체 cup, 술어 color, 값 teal, 범위 모든 등장 장면, 유지할 요소 [인물의 모습, 배경]).
해석 결과는 계획 미리보기에만 쓴다. 실제 수정은 사용자가 계획을 확인한 뒤 구조화된 값으로 요청한다.
구현: 규칙 기반(DSL), LLM.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from frame.domain.enums import ChangeScope
from frame.domain.errors import ValidationError
from frame.domain.ir import IntermediateRepresentation
from frame.domain.revision import KeepItem


class ChangeRequestNotUnderstood(ValidationError):
    """글로 쓴 변경 요청을 구조화된 변경으로 옮기지 못했다 (API 422)."""

    code = "CHANGE_REQUEST_NOT_UNDERSTOOD"


@dataclass(frozen=True)
class ChangeRequestInterpretation:
    entity_key: str | None  # None = 장면 수준 조건
    predicate: str
    new_value: str
    scope: ChangeScope | None = None  # 요청이 범위를 말하지 않았으면 None
    scene_keys: tuple[str, ...] = ()
    keep: tuple[KeepItem, ...] = ()
    notes: str = ""  # 어떻게 읽었는지 한두 문장 (화면에 보여 준다)


class ChangeRequestInterpreter(ABC):
    """계약
    - 성공: IR 에 있는 객체(또는 None)와 술어·값을 돌려준다. 값의 정규화와 범위 검증은 호출한 쪽이 한다.
    - 옮길 수 없으면 ChangeRequestNotUnderstood. 외부 호출 실패는 AdapterError 그대로 올린다.
    """

    @abstractmethod
    def interpret(
        self,
        ir: IntermediateRepresentation,
        text: str,
        *,
        entity_key: str | None = None,  # 화면에서 고른 객체 (있으면 해석의 실마리)
    ) -> ChangeRequestInterpretation: ...
