"""워커 유스케이스: QUEUED -> PROCESSING -> (생성·추적·검수) -> COMPLETED | FAILED.

- 검수 판정이 FAIL/UNDETERMINED 여도 실행이 끝나면 COMPLETED 이다.
- 실행 오류는 FAILED + error_code/error_message 로 남기고, 부모(원본) 버전은 건드리지 않는다.
- 실패해도 그때까지 만든 장면 클립·조립 영상·추적 결과와 진행 상황은 남긴다. 검수 결과는 남기지 않는다 (이슈 #3).
- 진행 상황과 끝난 장면 클립은 처리 도중에도 저장해, 비동기 실행 중에도 조회로 볼 수 있다.
- 생성 단계가 뽑은 정체성 기준 이미지는 클립과 같은 트랜잭션으로 저장한다 (ADR-0015). 재시작 복구로 다시 실행해도
  '클립은 있는데 그 클립에서 뽑은 기준이 없는' 상태가 되지 않고, 수정 버전이 기준을 물려받는다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from frame.domain.checks import CheckResult
from frame.domain.enums import VersionState
from frame.domain.errors import InvalidStateTransition, NotFound
from frame.domain.models import VideoVersion
from frame.domain.progress import StageProgress, format_elapsed_ms

from ..pipeline.context import PipelineContext
from ..pipeline.runner import PipelineFailure, PipelineRunner
from ..pipeline.stage import PipelineObserver, PipelineStage
from ..ports.common import Clock, IdGenerator
from ..ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)


def _save_derived_references(uow: UnitOfWork, version_id: str, ctx: PipelineContext) -> None:
    """이번 실행에서 새로 뽑은 정체성 기준 이미지 중 아직 저장하지 않은 것을 더한다."""
    if not ctx.derived_references:
        return
    stored = {r.reference_image_id for r in uow.reference_images.list_by_version(version_id)}
    for reference in ctx.derived_references:
        if reference.reference_image_id not in stored:
            uow.reference_images.add(reference)


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
        progress_writer = _ProgressWriter(self._uow_factory, version_id)
        try:
            self._runner.run(self._stages, ctx, observers=(progress_writer,))
        except PipelineFailure as failure:
            return self._record_failure(version_id, failure, ctx)
        return self._record_success(version_id, ctx)

    def _record_failure(
        self, version_id: str, failure: PipelineFailure, ctx: PipelineContext
    ) -> VideoVersion:
        message = _failure_message(failure, ctx)
        try:
            return self._save_failure(version_id, failure.error_code, message, ctx)
        except Exception:  # noqa: BLE001 - 부분 결과를 못 남겨도 FAILED 는 남겨야 한다 (PROCESSING 에 묶이지 않게)
            log.exception("버전 %s 의 부분 결과를 저장하지 못해 실패 상태만 기록합니다", version_id)
            return self._save_failure(version_id, failure.error_code, message, None)

    def _save_failure(
        self, version_id: str, error_code: str, message: str, ctx: PipelineContext | None
    ) -> VideoVersion:
        """ctx 를 주면 실패 전까지 만든 결과(장면 클립·조립 영상·추적 결과·진행)도 남긴다. 검수 결과는 남기지 않는다."""
        with self._uow_factory() as uow:
            version = uow.versions.get(version_id)
            assert version is not None
            if ctx is not None:
                version.clips = dict(ctx.clips)  # 끝난 장면(부모에서 재사용한 장면 포함)
                version.video_uri = ctx.video.uri if ctx.video is not None else None
                version.progress = ctx.progress
                for record in ctx.tracks:
                    uow.tracks.upsert(record)
                _save_derived_references(uow, version_id, ctx)
            version.mark_failed(error_code, message)
            uow.versions.save(version)
            uow.commit()
            return version

    def _record_success(self, version_id: str, ctx: PipelineContext) -> VideoVersion:
        with self._uow_factory() as uow:
            version = uow.versions.get(version_id)
            assert version is not None and ctx.video is not None
            version.video_uri = ctx.video.uri
            version.clips = dict(ctx.clips)
            version.progress = ctx.progress
            version.transition_to(VersionState.COMPLETED)
            for record in ctx.tracks:
                uow.tracks.upsert(record)
            _save_derived_references(uow, version_id, ctx)
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


class _ProgressWriter(PipelineObserver):
    """처리 도중의 진행 상황과 끝난 장면 클립을 저장한다 (thread 디스패처에서 처리 중에도 GET 으로 보이게).

    단계 시작·장면 진행·단계 완료 때마다 저장한다 (장면 하나가 수 분 걸리므로 비용은 무시할 만하다).
    저장이 실패해도 파이프라인은 계속되고(관찰자 오류는 기록만 됨), 최종 결과는 유스케이스가 따로 기록한다.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, version_id: str) -> None:
        self._uow_factory = uow_factory
        self._version_id = version_id

    def on_stage_started(self, stage: PipelineStage, ctx: PipelineContext) -> None:
        self._save(ctx)

    def on_stage_progress(self, stage: PipelineStage, ctx: PipelineContext) -> None:
        self._save(ctx)

    def on_stage_finished(
        self, stage: PipelineStage, ctx: PipelineContext, elapsed_ms: float
    ) -> None:
        self._save(ctx)

    def _save(self, ctx: PipelineContext) -> None:
        with self._uow_factory() as uow:
            version = uow.versions.get(self._version_id)
            if version is None or version.state is not VersionState.PROCESSING:
                return  # 다른 곳에서 상태가 바뀌었으면 덮어쓰지 않는다
            version.progress = ctx.progress
            version.clips = dict(ctx.clips)
            uow.versions.save(version)
            _save_derived_references(uow, self._version_id, ctx)
            uow.commit()


def _failure_message(failure: PipelineFailure, ctx: PipelineContext) -> str:
    """실패 단계·위치·단계별 소요 시간을 남긴다.

    예: '[generate] 원인 · 진행: 장면 closeup (2/3) 에서 실패 · 소요: generate 1.2s'
    """
    parts = [f"[{failure.stage}] {failure.message}"]
    location = _failure_location(ctx.progress.stage(failure.stage))
    if location is not None:
        parts.append(f"진행: {location}")
    if ctx.timings_ms:
        timings = ", ".join(
            f"{name} {format_elapsed_ms(ms)}" for name, ms in ctx.timings_ms.items()
        )
        parts.append(f"소요: {timings}")
    return " · ".join(parts)


def _failure_location(stage: StageProgress | None) -> str | None:
    """장면 단위로 일하는 단계(생성·추적)에서 어느 장면에서 멈췄는지. 항목 단위 진행이 없으면 None."""
    if stage is None or stage.total == 0:
        return None
    if stage.current is not None:
        return f"장면 {stage.current} ({stage.done + 1}/{stage.total}) 에서 실패"
    return f"장면 {stage.done}/{stage.total} 완료 뒤 실패"  # 예: 모든 장면을 만든 뒤 조립에서 실패
