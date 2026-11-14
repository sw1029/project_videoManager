"""US-06 2단계(비동기): 자연어 요구사항을 IR 로 구조화해 DRAFT 로 만들거나, 실패 시 FAILED 로 기록한다."""

from __future__ import annotations

from frame.domain.enums import VersionState
from frame.domain.errors import NotFound
from frame.domain.models import VideoVersion

from ..ports.structurer import ReferenceImageInput, RequirementStructurer, StructuringRequest
from ..ports.unit_of_work import UnitOfWorkFactory
from ..structuring_rules import check_reference_entities, check_target_duration


class StructureRequirements:
    def __init__(self, uow_factory: UnitOfWorkFactory, structurer: RequirementStructurer) -> None:
        self._uow_factory = uow_factory
        self._structurer = structurer

    def execute(self, version_id: str) -> VideoVersion:
        with self._uow_factory() as uow:
            version = uow.versions.get(version_id)
            if version is None:
                raise NotFound(f"버전 {version_id} 을 찾을 수 없습니다", version_id=version_id)
            if version.state is not VersionState.STRUCTURING:
                return version  # 중복 실행(재시도)에 대한 멱등성

            references = uow.reference_images.list_by_version(version_id)
            request = StructuringRequest(
                prompt=version.source_prompt or "",
                reference_images=tuple(
                    ReferenceImageInput(image_uri=r.image_uri, entity_key=r.entity_key)
                    for r in references
                ),
                target_duration_ms=version.target_duration_ms,
            )
            try:
                ir = self._structurer.structure(request)
                ir.validate()
                check_reference_entities((r.entity_key for r in references), ir)
                check_target_duration(ir.total_duration_ms, version.target_duration_ms)
                version.ir = ir
                version.transition_to(VersionState.DRAFT)
            except Exception as exc:  # noqa: BLE001 - 어떤 오류든 FAILED + 원인으로 남긴다
                version.mark_failed(getattr(exc, "code", "STRUCTURING_ERROR"), str(exc))
            uow.versions.save(version)
            uow.commit()
            return version
