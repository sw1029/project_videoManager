"""US-06 2단계(비동기): 자연어 요구사항을 IR 로 구조화해 DRAFT 로 만들거나, 실패 시 FAILED 로 기록한다."""

from __future__ import annotations

from frame.domain.enums import VersionState
from frame.domain.errors import NotFound, ValidationError
from frame.domain.models import VideoVersion

from ..ports.structurer import ReferenceImageInput, RequirementStructurer, StructuringRequest
from ..ports.unit_of_work import UnitOfWorkFactory

# 장면 길이 합계와 목표 길이의 허용 오차: 500 ms 또는 목표 길이의 10% 중 큰 값
DURATION_TOLERANCE_MS = 500
DURATION_TOLERANCE_RATIO = 0.1


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
                unknown = sorted(
                    {r.entity_key for r in references if r.entity_key}
                    - {e.entity_key for e in ir.entities}
                )
                if unknown:
                    raise ValidationError(
                        f"참조 이미지가 가리키는 객체가 구조화 결과에 없습니다: {unknown}"
                    )
                self._check_target_duration(ir.total_duration_ms, version.target_duration_ms)
                version.ir = ir
                version.transition_to(VersionState.DRAFT)
            except Exception as exc:  # noqa: BLE001 - 어떤 오류든 FAILED + 원인으로 남긴다
                version.mark_failed(getattr(exc, "code", "STRUCTURING_ERROR"), str(exc))
            uow.versions.save(version)
            uow.commit()
            return version

    @staticmethod
    def _check_target_duration(total_ms: int, target_ms: int | None) -> None:
        if target_ms is None:
            return
        tolerance = max(DURATION_TOLERANCE_MS, int(target_ms * DURATION_TOLERANCE_RATIO))
        if abs(total_ms - target_ms) > tolerance:
            raise ValidationError(
                f"장면 길이 합계 {total_ms} ms 가 목표 길이 {target_ms} ms 와 다릅니다 "
                f"(허용 오차 {tolerance} ms)"
            )
