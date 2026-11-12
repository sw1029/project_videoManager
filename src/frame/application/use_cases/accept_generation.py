"""생성 접수: DRAFT -> QUEUED 를 원자적으로 바꾸고 조건을 고정한 뒤 파이프라인 작업을 넘긴다 (202/409)."""

from __future__ import annotations

from datetime import UTC, datetime

from frame.domain.enums import VersionState
from frame.domain.errors import NotFound, VersionNotGeneratable

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
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._dispatcher = dispatcher
        self._now = clock.now if clock is not None else (lambda: datetime.now(UTC))

    def execute(self, version_id: str) -> AcceptGenerationResult:
        with self._uow_factory() as uow:
            version = uow.versions.get(version_id)
            if version is None:
                raise NotFound(f"버전 {version_id} 을 찾을 수 없습니다", version_id=version_id)
            if version.ir is None:
                raise VersionNotGeneratable("중간표현이 없는 버전은 생성할 수 없습니다")
            # 조건부 갱신: 동시에 두 번 접수돼도 하나만 성공한다 (무결성 규칙 1)
            if not uow.versions.try_transition(version_id, VersionState.DRAFT, VersionState.QUEUED):
                raise VersionNotGeneratable(
                    f"상태 {version.state} 인 버전은 생성을 접수할 수 없습니다 (DRAFT 만 가능)",
                    state=version.state,
                )
            version = uow.versions.get(version_id)
            assert version is not None
            version.freeze_conditions()
            version.record_queued(self._now())  # 접수 시각 (#2)
            uow.versions.save(version)
            uow.commit()

        self._dispatcher.dispatch(Job(RUN_GENERATION_PIPELINE, {"version_id": version_id}))
        return AcceptGenerationResult(version_id=version_id, state=VersionState.QUEUED)
