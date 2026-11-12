"""구체 단계: GenerateStage(선택적 재생성) -> TrackStage -> CheckStage."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from frame.domain.artifacts import GeneratedClip
from frame.domain.enums import ReferenceOrigin
from frame.domain.errors import IntegrityViolation
from frame.domain.ir import IntermediateRepresentation, SceneSpec
from frame.domain.models import ReferenceImage
from frame.domain.services.identity import (
    IdentityPolicy,
    entities_needing_identity,
    entity_appearance_fingerprint,
    identity_references_for,
    same_appearance,
)

from ..checks import CheckSuite
from ..ports.checker import CheckContext
from ..ports.common import IdGenerator
from ..ports.generator import (
    ParentClip,
    RevisionDirective,
    SceneGenerationRequest,
    VideoAssembler,
    VideoGenerator,
)
from ..ports.identity import IdentityExtractionRequest, IdentityReferenceExtractor
from ..ports.structurer import ReferenceImageInput
from ..ports.tracker import ObjectTracker, TrackingRequest
from .context import PipelineContext
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

    정체성 기준 이미지 (ADR-0015, identity 를 줬을 때만)
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
        self._identity = identity
        self._policy = identity_policy or IdentityPolicy()
        self._ids = ids

    def execute(self, ctx: PipelineContext) -> None:
        ir = ctx.ir
        ready = {scene.scene_key: self._ready_clip(ctx, scene) for scene in ir.scenes}
        self._derive_before_generation(ctx, ready)
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
            self._derive(ctx, ir, scene, clip)
        ctx.report_progress(total, total)  # 모든 장면 완료 -> 조립
        ordered = [ctx.clips[scene.scene_key] for scene in ir.scenes]
        ctx.video = self._assembler.assemble(ctx.version.version_id, ordered)

    # ---- 요청 만들기 ----
    def _request(
        self, ctx: PipelineContext, scene: SceneSpec, *, with_extras: bool
    ) -> SceneGenerationRequest:
        """with_extras=False 면 조건만 담는다 (재사용 판단용 fingerprint 계산)."""
        ir = ctx.ir
        request = SceneGenerationRequest(
            version_id=ctx.version.version_id,
            scene=scene,
            entities=tuple(ir.entity(key) for key in scene.entity_keys),
            references=tuple(
                ReferenceImageInput(image_uri=r.image_uri, entity_key=r.entity_key)
                for r in ctx.references
                if r.origin is ReferenceOrigin.USER
                and (r.entity_key is None or r.entity_key in scene.entity_keys)
            ),
            seed=ctx.version.seed_for(scene.scene_key),
        )
        if not with_extras:
            return request
        identity: tuple[ReferenceImageInput, ...] = ()
        if self._identity is not None:
            identity = tuple(
                ReferenceImageInput(r.image_uri, r.entity_key, r.description)
                for r in identity_references_for(
                    ir,
                    scene,
                    ctx.references,
                    self._policy,
                    scene_fingerprint=request.fingerprint,
                )
            )
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
        intent = ctx.version.intent
        if intent is None or not intent.targets(scene.scene_key):
            return None
        return RevisionDirective(
            kind=intent.kind,
            enforce=tuple(
                r
                for r in scene.requirements
                if r.entity_key == intent.entity_key
                and r.predicate == intent.predicate
                and r.expected_value == intent.value
            ),
            previous_value=intent.previous_value,
            keep=intent.keep_for(scene),
        )

    @staticmethod
    def _parent_clip(ctx: PipelineContext, scene: SceneSpec) -> ParentClip | None:
        parent = ctx.parent
        if parent is None or parent.ir is None or not parent.ir.has_scene(scene.scene_key):
            return None
        clip = parent.clips.get(scene.scene_key)
        if clip is None:
            return None
        parent_scene = parent.ir.scene(scene.scene_key)
        return ParentClip(
            version_id=parent.version_id,
            clip=clip,
            scene=parent_scene,
            entities=tuple(parent.ir.entity(key) for key in parent_scene.entity_keys),
        )

    def _ready_clip(self, ctx: PipelineContext, scene: SceneSpec) -> _Ready | None:
        fingerprint = self._request(ctx, scene, with_extras=False).fingerprint
        reusable = ctx.parent.clips.get(scene.scene_key) if ctx.parent is not None else None
        if reusable is not None and reusable.fingerprint == fingerprint:
            return _Ready(reusable, from_parent=True)
        own = ctx.version.clips.get(scene.scene_key)
        if own is not None and own.fingerprint == fingerprint:
            return _Ready(own, from_parent=False)
        return None

    # ---- 정체성 기준 이미지 ----
    def _derive_before_generation(
        self, ctx: PipelineContext, ready: dict[str, _Ready | None]
    ) -> None:
        if self._identity is None:
            return
        parent = ctx.parent
        for scene in ctx.ir.scenes:
            done = ready[scene.scene_key]
            if done is not None:
                self._derive(ctx, ctx.ir, scene, done.clip)
            elif parent is not None and parent.ir is not None:
                clip = parent.clips.get(scene.scene_key)
                if clip is not None and parent.ir.has_scene(scene.scene_key):
                    self._derive(ctx, parent.ir, parent.ir.scene(scene.scene_key), clip)

    def _derive(
        self,
        ctx: PipelineContext,
        clip_ir: IntermediateRepresentation,
        clip_scene: SceneSpec,
        clip: GeneratedClip,
    ) -> None:
        """clip(clip_ir 의 clip_scene 조건으로 만든 것)에서, 아직 쓸 기준이 없는 객체의 기준 이미지를 뽑는다."""
        if self._identity is None or not ctx.ir.has_scene(clip_scene.scene_key):
            return
        ir = ctx.ir
        scene = ir.scene(clip_scene.scene_key)
        wanted = [
            key
            for key in entities_needing_identity(ir, scene, ctx.references, self._policy)
            # 부모 클립에서 뽑을 때: 그 객체의 외형 조건이 지금과 같아야 지금 버전의 기준이 될 수 있다
            if clip_ir is ir or same_appearance(clip_ir, ir, scene.scene_key, key)
        ]
        if not wanted:
            return
        request = IdentityExtractionRequest(
            version_id=ctx.version.version_id,
            scene=clip_scene,
            clip=clip,
            entities=tuple(ir.entity(key) for key in wanted),
        )
        try:
            images = self._identity.extract(request)
        except Exception as exc:  # noqa: BLE001 - 기준 이미지는 보조 수단: 못 뽑아도 (과금된) 생성을 멈추지 않는다
            log.warning(
                "정체성 기준 이미지를 뽑지 못했습니다 (version=%s scene=%s 객체=%s): %s: %s",
                ctx.version.version_id,
                scene.scene_key,
                ", ".join(wanted),
                type(exc).__name__,
                exc,
            )
            return
        for image in images:
            if image.entity_key not in wanted:
                continue  # 계약 밖의 결과는 버린다
            ctx.add_reference(
                ReferenceImage(
                    reference_image_id=self._new_reference_id(ctx, scene, image.entity_key),
                    version_id=ctx.version.version_id,
                    image_uri=image.image_uri,
                    entity_key=image.entity_key,
                    origin=ReferenceOrigin.DERIVED,
                    source_scene_key=scene.scene_key,
                    source_fingerprint=clip.fingerprint,
                    appearance_fingerprint=entity_appearance_fingerprint(
                        ir, scene.scene_key, image.entity_key
                    ),
                    description=image.description,
                )
            )
            log.info(
                "정체성 기준 이미지: version=%s 객체=%s 장면=%s -> %s",
                ctx.version.version_id,
                image.entity_key,
                scene.scene_key,
                image.image_uri,
            )

    def _new_reference_id(self, ctx: PipelineContext, scene: SceneSpec, entity_key: str) -> str:
        if self._ids is not None:
            return self._ids.new_id("img")
        return f"img_{len(ctx.references) + 1}_{scene.scene_key}_{entity_key}"[:64]


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
        )
        outcomes = self._suite.run(check_ctx)
        valid_keys = {r.key for r in ctx.ir.iter_requirements()}
        for outcome in outcomes:  # 무결성 규칙 5: 결과와 요구조건은 같은 버전에 속해야 한다
            if outcome.requirement_key is not None and outcome.requirement_key not in valid_keys:
                raise IntegrityViolation(
                    f"검수 결과가 참조한 요구조건 '{outcome.requirement_key}' 이 이 버전에 없습니다"
                )
        ctx.check_outcomes.extend(outcomes)
