"""GET /api/vocabulary/predicates — 화면의 속성 블록(색상 변경·질감 변경 …)이 쓸 술어 목록."""

from __future__ import annotations

from fastapi import APIRouter

from .. import mappers
from ..schemas import VocabularyOut

router = APIRouter(prefix="/vocabulary", tags=["vocabulary"])


@router.get(
    "/predicates",
    response_model=VocabularyOut,
    summary="요구조건 술어 어휘(이름·값 종류·관계 여부·별칭)를 조회한다",
)
def list_predicates() -> VocabularyOut:
    return mappers.vocabulary_out()
