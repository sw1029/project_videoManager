"""요구사항 구조화 포트 (US-06). 구현 후보: 규칙 기반, LLM(API/로컬), 참조 이미지 기반(VLM).

- RequirementStructurer: 자연어(또는 DSL) 요구사항 -> IR. 비동기 구조화 작업이 쓴다.
- IrDocumentStructurer: 화면이 블록으로 조립한 IR 문서 -> IR. LLM 없이 보정·검증만 하므로
  요청 안에서 바로 쓴다 (#10 #11).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from frame.domain.ir import IntermediateRepresentation


@dataclass(frozen=True)
class ReferenceImageInput:
    image_uri: str
    entity_key: str | None = None
    # 이미지에서 읽은 외형 설명. 정체성 기준 이미지에만 쓰이고 프롬프트를 보강한다 (ADR-0015)
    description: str = ""


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


@dataclass(frozen=True)
class StructuredDocument:
    """IR 문서를 보정·검증한 결과."""

    ir: IntermediateRepresentation
    # 보정이 바꾼 것 (구간 이동·객체 연결 등). 화면에 그대로 보여 준다
    repair_notes: tuple[str, ...] = ()


class IrDocumentStructurer(ABC):
    """IR 문서(IR.to_dict 형식)를 결정적 보정 -> 검증만 거쳐 IR 로 만든다 (IR 직접 입력·DRAFT IR 수정).

    계약
    - 성공: validate() 를 통과한 IR 과 보정 메모를 돌려준다. 같은 문서에 같은 결과 (결정적).
      입력 문서는 바꾸지 않는다.
    - 고칠 수 없는 문서(필수 항목 누락·형식 위반·무결성 규칙 위반)는 ValidationError (API 422).
      메시지에 원인을 담는다.
    """

    @abstractmethod
    def structure_document(self, document: Mapping[str, Any]) -> StructuredDocument: ...
