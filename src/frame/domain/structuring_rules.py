"""구조화 결과(IR)가 버전에 담기기 전에 지켜야 할 규칙 (도메인 규칙, 포트를 쓰지 않는다).

IR 자체의 무결성은 IR.validate() 가 보고, 여기서는 버전의 다른 값(참조 이미지·목표 길이)과 맞는지를 본다.
자연어 구조화(StructureRequirements)·IR 직접 입력(CreateProject)·DRAFT IR 수정(EditDraftIr)이 함께 쓴다.
"""

from __future__ import annotations

from collections.abc import Iterable

from .errors import ValidationError
from .ir import IntermediateRepresentation

# 장면 길이 합계와 목표 길이의 허용 오차: 500 ms 또는 목표 길이의 10% 중 큰 값
DURATION_TOLERANCE_MS = 500
DURATION_TOLERANCE_RATIO = 0.1


def check_reference_entities(
    reference_entity_keys: Iterable[str | None], ir: IntermediateRepresentation
) -> None:
    """참조 이미지가 가리키는 객체가 IR 에 있어야 한다 (객체를 지정하지 않은 참조는 상관없다)."""
    unknown = sorted(
        {key for key in reference_entity_keys if key} - {e.entity_key for e in ir.entities}
    )
    if unknown:
        raise ValidationError(f"참조 이미지가 가리키는 객체가 구조화 결과에 없습니다: {unknown}")


def check_target_duration(total_ms: int, target_ms: int | None) -> None:
    """목표 길이를 줬으면 장면 길이 합계가 허용 오차 안이어야 한다."""
    if target_ms is None:
        return
    tolerance = max(DURATION_TOLERANCE_MS, int(target_ms * DURATION_TOLERANCE_RATIO))
    if abs(total_ms - target_ms) > tolerance:
        raise ValidationError(
            f"장면 길이 합계 {total_ms} ms 가 목표 길이 {target_ms} ms 와 다릅니다 "
            f"(허용 오차 {tolerance} ms)"
        )
