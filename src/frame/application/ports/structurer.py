"""요구사항 구조화 포트 (US-06). 구현 후보: 규칙 기반, LLM(API/로컬), 참조 이미지 기반(VLM)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from frame.domain.ir import IntermediateRepresentation


@dataclass(frozen=True)
class ReferenceImageInput:
    image_uri: str
    entity_key: str | None = None


@dataclass(frozen=True)
class StructuringRequest:
    prompt: str
    reference_images: tuple[ReferenceImageInput, ...] = ()
    target_duration_ms: int | None = None


class RequirementStructurer(ABC):
    """자연어 요구사항(+참조 이미지)을 IR 로 변환한다.

    계약
    - 성공: 유효한(validate() 통과) IR 을 돌려준다.
    - 실패: StructuringError 를 던진다. (필수 항목 누락 등은 메시지에 원인을 담는다)
    - 결정성: 같은 입력에 같은 IR 을 돌려주는 것이 바람직하다 (LLM 은 temperature=0 권장).
    """

    @abstractmethod
    def structure(self, request: StructuringRequest) -> IntermediateRepresentation: ...
