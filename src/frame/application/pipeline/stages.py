"""구체 단계: GenerateStage(선택적 재생성) -> TrackStage -> CheckStage."""

from __future__ import annotations

from frame.domain.errors import IntegrityViolation

from ..checks import CheckSuite
from ..ports.checker import CheckContext
from ..ports.generator import SceneGenerationRequest, VideoAssembler, VideoGenerator
from ..ports.structurer import ReferenceImageInput
from ..ports.tracker import ObjectTracker, TrackingRequest
from .context import PipelineContext
from .stage import PipelineStage


class GenerateStage(PipelineStage):
    """장면 단위 생성. 부모 버전에 같은 조건(fingerprint)의 클립이 있으면 재사용한다.

    이것이 US-01 '비대상 장면 보존' 의 1차 보장 장치이다: 바뀌지 않은 장면은 바이트 단위로 동일하다.
    """

    name = "generate"

    def __init__(self, generator: VideoGenerator, assembler: VideoAssembler) -> None:
        self._generator = generator
        self._assembler = assembler

    def execute(self, ctx: PipelineContext) -> None:
        ir = ctx.ir
        references = tuple(
            ReferenceImageInput(image_uri=r.image_uri, entity_key=r.entity_key)
            for r in ctx.references
        )
        parent_clips = ctx.parent.clips if ctx.parent is not None else {}
        for scene in ir.scenes:
            request = SceneGenerationRequest(
                version_id=ctx.version.version_id,
                scene=scene,
                entities=tuple(ir.entity(key) for key in scene.entity_keys),
                references=tuple(
                    r
                    for r in references
                    if r.entity_key is None or r.entity_key in scene.entity_keys
                ),
                seed=ctx.version.seed,
            )
            reusable = parent_clips.get(scene.scene_key)
            if reusable is not None and reusable.fingerprint == request.fingerprint:
                ctx.clips[scene.scene_key] = reusable
                ctx.reused_scene_keys.add(scene.scene_key)
                continue
            ctx.clips[scene.scene_key] = self._generator.generate_scene(request)
            ctx.changed_scene_keys.add(scene.scene_key)
        ordered = [ctx.clips[scene.scene_key] for scene in ir.scenes]
        ctx.video = self._assembler.assemble(ctx.version.version_id, ordered)


class TrackStage(PipelineStage):
    name = "track"

    def __init__(self, tracker: ObjectTracker) -> None:
        self._tracker = tracker

    def execute(self, ctx: PipelineContext) -> None:
        for scene in ctx.ir.scenes:
            if not scene.entity_keys:
                continue
            request = TrackingRequest(
                version_id=ctx.version.version_id,
                scene=scene,
                clip=ctx.clips[scene.scene_key],
                entity_keys=scene.entity_keys,
            )
            ctx.tracks.extend(self._tracker.track(request))


class CheckStage(PipelineStage):
    name = "check"

    def __init__(self, suite: CheckSuite) -> None:
        self._suite = suite

    def execute(self, ctx: PipelineContext) -> None:
        check_ctx = CheckContext(
            version=ctx.version,
            ir=ctx.ir,
            clips=dict(ctx.clips),
            tracks=tuple(ctx.tracks),
            parent=ctx.parent,
            parent_ir=ctx.parent.ir if ctx.parent is not None else None,
            parent_clips=dict(ctx.parent.clips) if ctx.parent is not None else {},
            changed_scene_keys=frozenset(ctx.changed_scene_keys),
            reused_scene_keys=frozenset(ctx.reused_scene_keys),
        )
        outcomes = self._suite.run(check_ctx)
        valid_keys = {r.key for r in ctx.ir.iter_requirements()}
        for outcome in outcomes:  # 무결성 규칙 5: 결과와 요구조건은 같은 버전에 속해야 한다
            if outcome.requirement_key is not None and outcome.requirement_key not in valid_keys:
                raise IntegrityViolation(
                    f"검수 결과가 참조한 요구조건 '{outcome.requirement_key}' 이 이 버전에 없습니다"
                )
        ctx.check_outcomes.extend(outcomes)
