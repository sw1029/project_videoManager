"""DRAFT IR 수정 (#11, 문제정의서 리스크 2 "구조화 결과의 사용자 확인·수정"): PUT /api/versions/{id}/ir.

화면이 블록으로 고친 IR 문서를 보정 -> 검증한 뒤 DRAFT 버전에 저장한다. 원문(source_prompt)·seed·참조 이미지는 그대로다.
- DRAFT 이고 조건이 고정되지 않은 버전만 고칠 수 있다. 그 밖에는 409 VERSION_NOT_EDITABLE
  (생성이 접수되면 조건이 고정되므로, 고정된 뒤의 변경은 POST /revisions 로 새 버전을 만든다).
- 저장은 조건부 갱신이다: 읽은 뒤에 다른 요청이 생성을 접수했으면 저장하지 않고 409 를 낸다
  (고정된 IR 은 바뀌지 않는다).
- 목표 길이는 확인하지 않는다. 목표 길이는 자동 구조화에 주는 조건이고, 사람이 고친 길이가 곧 목표다.
"""

from __future__ import annotations

from frame.domain.errors import NotFound, VersionNotEditable
from frame.domain.models import VideoVersion
from frame.domain.state_machine import EDITABLE_STATES

from ..dto import EditDraftIrCommand, EditDraftIrResult
from ..ports.structurer import IrDocumentStructurer
from ..ports.unit_of_work import UnitOfWorkFactory
from ..structuring_rules import check_reference_entities


class EditDraftIr:
    def __init__(self, uow_factory: UnitOfWorkFactory, structurer: IrDocumentStructurer) -> None:
        self._uow_factory = uow_factory
        self._structurer = structurer

    def execute(self, command: EditDraftIrCommand) -> EditDraftIrResult:
        with self._uow_factory() as uow:
            version = uow.versions.get(command.version_id)
            if version is None:
                raise NotFound(
                    f"버전 {command.version_id} 을 찾을 수 없습니다", version_id=command.version_id
                )
            _ensure_editable(version)
            structured = self._structurer.structure_document(command.ir)  # 고칠 수 없으면 422
            references = uow.reference_images.list_by_version(version.version_id)
            check_reference_entities((r.entity_key for r in references), structured.ir)
            if not uow.versions.replace_draft_ir(version.version_id, structured.ir):
                current = uow.versions.get(version.version_id)
                raise VersionNotEditable(
                    "중간표현을 고치는 사이에 생성이 접수되어 조건이 고정되었습니다. "
                    "바꾸려면 수정 버전을 만드세요",
                    state=current.state if current is not None else None,
                )
            uow.commit()
        return EditDraftIrResult(
            version_id=version.version_id,
            state=version.state,
            repair_notes=structured.repair_notes,
        )


def _ensure_editable(version: VideoVersion) -> None:
    if version.state not in EDITABLE_STATES or version.is_frozen or version.ir is None:
        raise VersionNotEditable(
            f"상태 {version.state} 인 버전의 중간표현은 고칠 수 없습니다 (DRAFT 만 가능). "
            "생성이 접수된 뒤에는 수정 버전을 만드세요",
            state=version.state,
        )
