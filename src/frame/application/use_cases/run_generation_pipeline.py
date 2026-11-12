"""워커 유스케이스: QUEUED -> PROCESSING -> (생성·추적·검수) -> COMPLETED | FAILED.

- 검수 판정이 FAIL/UNDETERMINED 여도 실행이 끝나면 COMPLETED 이다.
- 실행 오류는 FAILED + error_code/error_message 로 남기고, 부모(원본) 버전은 건드리지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from frame.domain.checks import CheckResult
from frame.domain.enums import VersionState
from frame.domain.errors import InvalidStateTransition, NotFound
from frame.domain.models import VideoVersion

from ..pipeline.context import PipelineContext
from ..pipeline.runner import PipelineFailure, PipelineRunner
from ..pipeline.stage import PipelineStage
from ..ports.common import Clock, IdGenerator
from ..ports.unit_of_work import UnitOfWorkFactory


class RunGenerationPipeline:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        stages: Sequence[PipelineStage],
        runner: PipelineRunner,
        ids: IdGenerator,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._stages = tuple(stages)
        self._runner = runner
        self._ids = ids
        self._now = clock.now if clock is not None else (lambda: datetime.now(UTC))

    def execute(self, version_id: str) -> VideoVersion:
        with self._uow_factory() as uow:
            version = uow.versions.get(version_id)
            if version is None:
                raise NotFound(f"버전 {version_id} 을 찾을 수 없습니다", version_id=version_id)
            if not uow.versions.try_transition(
                version_id, VersionState.QUEUED, VersionState.PROCESSING
            ):
                raise InvalidStateTransition(
                    f"상태 {version.state} 인 버전은 실행할 수 없습니다 (QUEUED 만 가능)"
                )
            version = uow.versions.get(version_id)
            assert version is not None
            # 시작 시각·시도 횟수를 단계 실행 전에 커밋한다: 도중에 프로세스가 죽어도 복구가 판단할 수 있다 (#2)
            version.record_started(self._now())
            uow.versions.save(version)
            parent = (
                uow.versions.get(version.parent_version_id) if version.parent_version_id else None
            )
            references = tuple(uow.reference_images.list_by_version(version_id))
            uow.commit()

        ctx = PipelineContext(version=version, references=references, parent=parent)
        try:
            self._runner.run(self._stages, ctx)
        except PipelineFailure as failure:
            return self._record_failure(version_id, failure)
        return self._record_success(version_id, ctx)

    def _record_failure(self, version_id: str, failure: PipelineFailure) -> VideoVersion:
        with self._uow_factory() as uow:
            version = uow.versions.get(version_id)
            assert version is not None
            version.mark_failed(failure.error_code, f"[{failure.stage}] {failure.message}")
            uow.versions.save(version)
            uow.commit()
            return version

    def _record_success(self, version_id: str, ctx: PipelineContext) -> VideoVersion:
        with self._uow_factory() as uow:
            version = uow.versions.get(version_id)
            assert version is not None and ctx.video is not None
            version.video_uri = ctx.video.uri
            version.clips = dict(ctx.clips)
            version.transition_to(VersionState.COMPLETED)
            for record in ctx.tracks:
                uow.tracks.upsert(record)
            uow.checks.add_all(
                [
                    CheckResult(
                        check_id=self._ids.new_id("chk"),
                        version_id=version_id,
                        check_type=o.check_type,
                        verdict=o.verdict,
                        score=o.score,
                        requirement_key=o.requirement_key,
                        entity_key=o.entity_key,
                        scene_key=o.scene_key,
                        warnings=tuple(o.warnings),
                        detail=dict(o.detail),
                    )
                    for o in ctx.check_outcomes
                ]
            )
            uow.versions.save(version)
            uow.commit()
            return version
