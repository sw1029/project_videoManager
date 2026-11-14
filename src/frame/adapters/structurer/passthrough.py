"""IR 직접 입력 모드 (STRUCTURING_DESIGN §3, #10 #11): 화면이 블록으로 조립한 IR 문서를 LLM 없이 IR 로 만든다.

보정(light_repair) -> IR 변환 -> 검증(validate) 만 한다. LLM 구조화와 같은 보정 규칙을 쓰므로
입력 모드가 달라도 같은 IR 규칙으로 수렴한다. 고친 것은 보정 메모로 돌려준다 (구조화 보고서는 보류, #16).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from frame.application.ports.structurer import IrDocumentStructurer, StructuredDocument
from frame.domain.errors import ValidationError
from frame.domain.ir import IntermediateRepresentation

from .repair import light_repair_with_notes


class PassthroughStructurer(IrDocumentStructurer):
    def structure_document(self, document: Mapping[str, Any]) -> StructuredDocument:
        if not isinstance(document, Mapping):
            raise ValidationError("IR 문서는 JSON 객체여야 합니다")
        # 보정과 변환은 형식이 맞는 문서를 전제로 짰다. 어디서 틀려도 포트 계약대로 ValidationError 로 알린다 (감사 8)
        try:
            repaired, notes = light_repair_with_notes(dict(document))  # 깊은 복사본을 고친다
            ir = IntermediateRepresentation.from_dict(repaired)
        except KeyError as exc:
            raise ValidationError(f"IR 문서에 필수 항목이 없습니다: {exc}") from exc
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError(f"IR 문서의 형식이 올바르지 않습니다: {exc}") from exc
        ir.validate()
        return StructuredDocument(ir=ir, repair_notes=tuple(notes))
