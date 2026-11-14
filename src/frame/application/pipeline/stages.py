"""구체 단계: GenerateStage(선택적 재생성) -> TrackStage -> CheckStage.

생성 단계는 '재사용할 것 고르기 -> 생성 -> 조립' 을 한다. 정체성 기준 이미지를 뽑고 고르는 일은
identity_anchoring.py 의 IdentityAnchoring 이 맡는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from frame.domain.artifacts import GeneratedClip
from frame.domain.errors import IntegrityViolation
from frame.domain.ir import SceneSpec
from frame.domain.services.identity import (
    IdentityPolicy,
)

from ..checks import CheckSuite
from ..ports.assembler import VideoAssembler
from ..ports.checker import CheckContext
from ..ports.common import IdGenerator
from ..ports.generator import (
    ParentClip,
    RevisionDirective,
    SceneGenerationRequest,
    VideoGenerator,
)
from ..ports.identity import IdentityReferenceExtractor
from ..ports.tracker import ObjectTracker, TrackingRequest
from .context import PipelineContext
from .identity_anchoring import IdentityAnchoring
from .requests import condition_request, parent_clip_of, revision_directive
from .stage import PipelineStage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Ready:
    """다시 만들지 않아도 되는 장면 클립."""

    clip: GeneratedClip
    from_parent: bool  # 참이면 부모 클립 재사용, 거짓이면 이 버전이 앞선 시도에서 만든 클립


class GenerateStage(PipelineStage):
    """장면 단위 생성. 부모 버전에 같은 조건(fingerprint)의 클립이 있으면 재사용한다.

    이것이 US-01 '비대상 장면 보존' 의 1차 보장 장치이다: 바뀌지 않은 장면은 바이트 단위로 동일하다.

    정체성 기준 이미지 (ADR-0015, identity 를 줬을 때만. 뽑고 고르는 일은 IdentityAnchoring 이 한다)
    - 사용자 참조가 없는 객체는 그 객체가 나온 클립에서 기준 이미지를 뽑아 버전의 참조 이미지로 남기고,
      그 객체가 나오는 다른 장면을 생성할 때 identity_references 로 넘긴다.
    - 생성하기 전에 이미 손에 있는 클립에서 먼저 뽑는다: 재사용할 장면의 클립, 그리고 다시 만들 장면의 부모 클립
      (외형 조건이 바뀌지 않은 객체만). 그래서 수정 버전에서 다시 만드는 장면도 부모와 같은 모습을 이어 간다.
    - 재사용 판단은 조건 fingerprint 만 본다. 기준 이미지는 판단에 끼지 않으므로 바뀌지 않은 장면은 그대로 재사용된다.
    - 기준 이미지를 못 뽑아도 생성은 계속한다 (기록만 남긴다).

    부모 클립 (ADR-0016)
    - 조건이 바뀌어 다시 만드는 장면에 부모 클립과 부모 쪽 장면 조건을 함께 넘긴다. 편집을 지원하는 생성기가 쓴다.

    수정 의도 (ADR-0017)
    - 장면의 seed 는 버전이 장면별로 다시 지정한 값이 있으면 그것을 쓴다. REPAIR 는 요구조건을 바꾸지 않으므로
      대상 장면의 seed 를 바꿔 부모 클립이 재사용되지 않게 한다.
    - 수정 버전이 다시 만들기로 한 장면에는 수정 지시(꼭 지켜야 할 요구조건·그대로 둘 요소)를 함께 넘긴다.

    다시 실행 (재시작 복구)
    - 이 버전이 앞선 시도에서 이미 만든 클립은 조건이 같으면 다시 만들지 않는다.
    """

    name = "generate"

    def __init__(
        self,
        generator: VideoGenerator,
        assembler: VideoAssembler,
        *,
        identity: IdentityReferenceExtractor | None = None,
        identity_policy: IdentityPolicy | None = None,
        ids: IdGenerator | None = None,
    ) -> None:
        self._generator = generator
        self._assembler = assembler
        self._anchoring = IdentityAnchoring(identity, identity_policy, ids)

    def execute(self, ctx: PipelineContext) -> None:
        ir = ctx.ir
        ready = {scene.scene_key: self._ready_clip(ctx, scene) for scene in ir.scenes}
        self._anchoring.derive_before_generation(
            ctx, {key: done.clip if done is not None else None for key, done in ready.items()}
        )
        total = len(ir.scenes)
        for index, scene in enumerate(ir.scenes):
            ctx.report_progress(index, total, scene.scene_key)
            done = ready[scene.scene_key]
            if done is not None:
                ctx.clips[scene.scene_key] = done.clip
                (ctx.reused_scene_keys if done.from_parent else ctx.changed_scene_keys).add(
                    scene.scene_key
                )
                continue
            clip = self._generator.generate_scene(self._request(ctx, scene, with_extras=True))
            ctx.clips[scene.scene_key] = clip
            ctx.changed_scene_keys.add(scene.scene_key)
            self._anchoring.derive(ctx, ir, scene, clip)
        ctx.report_progress(total, total)  # 모든 장면 완료 -> 조립
        ordered = [ctx.clips[scene.scene_key] for scene in ir.scenes]
        ctx.video = self._assembler.assemble(ctx.version.version_id, ordered)

    # ---- 요청 만들기 ----
    def _request(
        self, ctx: PipelineContext, scene: SceneSpec, *, with_extras: bool
    ) -> SceneGenerationRequest:
        """with_extras=False 면 조건만 담는다 (재사용 판단용 fingerprint 계산)."""
        ir = ctx.ir
        request = condition_request(ctx.version, ir, scene, ctx.references)
        if not with_extras:
            return request
        identity = self._anchoring.references_for(ctx, scene, request.fingerprint)
        return SceneGenerationRequest(
            version_id=request.version_id,
            scene=request.scene,
            entities=request.entities,
            references=request.references,
            seed=request.seed,
            identity_references=identity,
            parent=self._parent_clip(ctx, scene),
            directive=self._directive(ctx, scene),
        )

    @staticmethod
    def _directive(ctx: PipelineContext, scene: SceneSpec) -> RevisionDirective | None:
        """수정 버전이 이 장면을 다시 만들기로 했으면 그 지시. 그 밖의 장면·원본 버전은 None."""
        return revision_directive(ctx.version.intent, scene)

    @staticmethod
    def _parent_clip(ctx: PipelineContext, scene: SceneSpec) -> ParentClip | None:
        return parent_clip_of(ctx.parent, scene)

    def _ready_clip(self, ctx: PipelineContext, scene: SceneSpec) -> _Ready | None:
        fingerprint = self._request(ctx, scene, with_extras=False).fingerprint
        reusable = ctx.parent.clips.get(scene.scene_key) if ctx.parent is not None else None
        if reusable is not None and reusable.fingerprint == fingerprint:
            return _Ready(reusable, from_parent=True)
        own = ctx.version.clips.get(scene.scene_key)
        if own is not None and own.fingerprint == fingerprint:
            return _Ready(own, from_parent=False)
        return None


class TrackStage(PipelineStage):
    name = "track"

    def __init__(self, tracker: ObjectTracker) -> None:
        self._tracker = tracker

    def execute(self, ctx: PipelineContext) -> None:
        targets = [scene for scene in ctx.ir.scenes if scene.entity_keys]
        for index, scene in enumerate(targets):
            ctx.report_progress(index, len(targets), scene.scene_key)
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
            references=tuple(ctx.references),
            parent_checks=tuple(ctx.parent_checks),
        )
        outcomes = self._suite.run(check_ctx)
        valid_keys = {r.key for r in ctx.ir.iter_requirements()}
        for outcome in outcomes:  # 무결성 규칙 5: 결과와 요구조건은 같은 버전에 속해야 한다
            if outcome.requirement_key is not None and outcome.requirement_key not in valid_keys:
                raise IntegrityViolation(
                    f"검수 결과가 참조한 요구조건 '{outcome.requirement_key}' 이 이 버전에 없습니다"
                )
        ctx.check_outcomes.extend(outcomes)
