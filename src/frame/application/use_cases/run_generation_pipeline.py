"""워커 유스케이스: QUEUED -> PROCESSING -> (생성·추적·검수) -> COMPLETED | FAILED.

- 검수 판정이 FAIL/UNDETERMINED 여도 실행이 끝나면 COMPLETED 이다.
- 실행 오류는 FAILED + error_code/error_message 로 남기고, 부모(원본) 버전은 건드리지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence

from frame.domain.checks import CheckResult
from frame.domain.enums import VersionState
from frame.domain.errors import InvalidStateTransition, NotFound
from frame.domain.models import VideoVersion
from frame.domain.progress import format_elapsed_ms

from ..pipeline.context import PipelineContext
from ..pipeline.runner import PipelineFailure, PipelineRunner
from ..pipeline.stage import PipelineStage
from ..ports.common import IdGenerator
from ..ports.unit_of_work import UnitOfWorkFactory


class RunGenerationPipeline:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        stages: Sequence[PipelineStage],
        runner: PipelineRunner,
        ids: IdGenerator,
    ) -> None:
        self._uow_factory = uow_factory
        self._stages = tuple(stages)
        self._runner = runner
        self._ids = ids

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
            parent = (
                uow.versions.get(version.parent_version_id) if version.parent_version_id else None
            )
            references = tuple(uow.reference_images.list_by_version(version_id))
            uow.commit()

        ctx = PipelineContext(version=version, references=references, parent=parent)
        try:
            self._runner.run(self._stages, ctx)
        except PipelineFailure as failure:
            return self._record_failure(version_id, failure, ctx)
        return self._record_success(version_id, ctx)

    def _record_failure(
        self, version_id: str, failure: PipelineFailure, ctx: PipelineContext
    ) -> VideoVersion:
        with self._uow_factory() as uow:
            version = uow.versions.get(version_id)
            assert version is not None
            version.mark_failed(failure.error_code, _failure_message(failure, ctx))
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


def _failure_message(failure: PipelineFailure, ctx: PipelineContext) -> str:
    """'[단계] 원인 · 소요: generate 1.2s, track 30ms' 처럼 실패 단계와 단계별 소요 시간을 남긴다."""
    parts = [f"[{failure.stage}] {failure.message}"]
    if ctx.timings_ms:
        timings = ", ".join(
            f"{name} {format_elapsed_ms(ms)}" for name, ms in ctx.timings_ms.items()
        )
        parts.append(f"소요: {timings}")
    return " · ".join(parts)
