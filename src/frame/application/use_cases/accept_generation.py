"""생성 접수: DRAFT -> QUEUED 를 원자적으로 바꾸고 조건을 고정한 뒤 파이프라인 작업을 넘긴다 (202/409)."""

from __future__ import annotations

from frame.domain.enums import VersionState
from frame.domain.errors import NotFound, ValidationError, VersionNotGeneratable
from frame.domain.ir import IntermediateRepresentation

from ..dto import AcceptGenerationResult
from ..jobs import RUN_GENERATION_PIPELINE
from ..ports.common import Clock
from ..ports.jobs import Job, JobDispatcher
from ..ports.unit_of_work import UnitOfWorkFactory


class AcceptGeneration:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        dispatcher: JobDispatcher,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._dispatcher = dispatcher
        self._now = clock.now

    def execute(self, version_id: str) -> AcceptGenerationResult:
        with self._uow_factory() as uow:
            version = uow.versions.get(version_id)
            if version is None:
                raise NotFound(f"버전 {version_id} 을 찾을 수 없습니다", version_id=version_id)
            if version.ir is None:
                raise VersionNotGeneratable("중간표현이 없는 버전은 생성할 수 없습니다")
            _require_storable_keys(version_id, version.ir)
            # 조건부 갱신: 동시에 두 번 접수돼도 하나만 성공한다 (무결성 규칙 1)
            if not uow.versions.try_transition(version_id, VersionState.DRAFT, VersionState.QUEUED):
                raise VersionNotGeneratable(
                    f"상태 {version.state} 인 버전은 생성을 접수할 수 없습니다 (DRAFT 만 가능)",
                    state=version.state,
                )
            version = uow.versions.get(version_id)
            if version is None:  # 방금 상태를 바꾼 버전이 다시 읽히지 않는다 (저장소가 어긋났다)
                raise NotFound(f"버전 {version_id} 을 찾을 수 없습니다", version_id=version_id)
            version.freeze_conditions()
            version.record_queued(self._now())  # 접수 시각 (#2)
            uow.versions.save(version)
            uow.commit()

        self._dispatcher.dispatch(Job(RUN_GENERATION_PIPELINE, {"version_id": version_id}))
        return AcceptGenerationResult(version_id=version_id, state=VersionState.QUEUED)


def _require_storable_keys(version_id: str, ir: IntermediateRepresentation) -> None:
    """키 규칙에 어긋난 IR 은 접수하지 않는다 (그 규칙이 생기기 전에 저장된 행 — 감사 P-20, 델타 B10-D02 · D06).

    객체 · 장면 키는 클립과 기준 이미지의 저장 키에 그대로 들어간다. 접수한 뒤에 막으면 늦다: 다른 자리를 가리키는
    키는 저장소가 막지만 클립을 쓸 때라 이미 생성기를 부른 뒤이고, 대소문자만 다른 키는 저장소가 막지 못한다
    (대소문자를 가리지 않는 파일 시스템에서 두 클립이 한 파일이 된다). 키가 아닌 규칙은 다시 보지 않는다 —
    규칙이 늘 때마다 그 전에 저장된 버전이 막히지 않게 한다.
    """
    try:
        ir.validate_keys()
    except ValidationError as exc:
        raise VersionNotGeneratable(
            f"이 버전의 중간표현은 키 규칙에 맞지 않아 생성할 수 없습니다 ({exc.message}). "
            "키 규칙이 생기기 전에 저장된 버전으로 보입니다. 중간표현의 키를 고쳐 저장한 뒤 다시 접수하세요",
            version_id=version_id,
        ) from exc
