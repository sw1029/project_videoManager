"""정체성 기준 이미지 뽑기와 고르기 (ADR-0015): 생성 단계의 협력 객체.

사용자 참조가 없는 객체는 그 객체가 나온 클립에서 기준 이미지를 뽑아 이 실행의 참조 목록에 더하고(derive),
그 객체가 나오는 다른 장면을 만들 때 넘길 기준을 고른다(references_for). 규칙 자체(누구에게 필요한가, 아직 유효한가)는
도메인(domain/identity.py)에 있고, 여기서는 그 규칙으로 추출기(IdentityReferenceExtractor 포트)를 부린다.

- 기준 이미지는 보조 수단이다: 못 뽑아도 (과금된) 생성을 멈추지 않는다. 기록만 남긴다.
- 추출기를 주지 않으면 (FRAME_IDENTITY=off) 아무 일도 하지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from frame.domain.artifacts import GeneratedClip
from frame.domain.enums import ReferenceOrigin
from frame.domain.identity import (
    IdentityPolicy,
    entities_needing_identity,
    entity_appearance_fingerprint,
    identity_references_for,
    same_appearance,
)
from frame.domain.ir import IntermediateRepresentation, SceneSpec
from frame.domain.models import ReferenceImage

from ..ports.common import IdGenerator
from ..ports.identity import IdentityExtractionRequest, IdentityReferenceExtractor
from ..ports.structurer import ReferenceImageInput
from .context import PipelineContext

log = logging.getLogger(__name__)


class IdentityAnchoring:
    def __init__(
        self,
        extractor: IdentityReferenceExtractor | None,
        policy: IdentityPolicy | None = None,
        ids: IdGenerator | None = None,
    ) -> None:
        self._extractor = extractor
        self._policy = policy or IdentityPolicy()
        self._ids = ids

    def references_for(
        self, ctx: PipelineContext, scene: SceneSpec, scene_fingerprint: str
    ) -> tuple[ReferenceImageInput, ...]:
        """이 장면을 만들 때 생성기에 넘길 기준 이미지 (이 실행의 참조 목록에서 고른다)."""
        if self._extractor is None:
            return ()
        return tuple(
            ReferenceImageInput(r.image_uri, r.entity_key, r.description)
            for r in identity_references_for(
                ctx.ir,
                scene,
                ctx.references,
                self._policy,
                scene_fingerprint=scene_fingerprint,
            )
        )

    def derive_before_generation(
        self, ctx: PipelineContext, ready: Mapping[str, GeneratedClip | None]
    ) -> None:
        """생성하기 전에, 이미 손에 있는 클립에서 먼저 뽑는다.

        ready 는 장면마다 다시 만들지 않아도 되는 클립(없으면 None)이다. 그런 장면은 그 클립에서, 다시 만들 장면은
        부모 클립에서 뽑는다 (외형 조건이 바뀌지 않은 객체만 - derive 가 가린다).
        """
        if self._extractor is None:
            return
        parent = ctx.parent
        for scene in ctx.ir.scenes:
            done = ready[scene.scene_key]
            if done is not None:
                self.derive(ctx, ctx.ir, scene, done)
            elif parent is not None and parent.ir is not None:
                clip = parent.clips.get(scene.scene_key)
                if clip is not None and parent.ir.has_scene(scene.scene_key):
                    self.derive(ctx, parent.ir, parent.ir.scene(scene.scene_key), clip)

    def derive(
        self,
        ctx: PipelineContext,
        clip_ir: IntermediateRepresentation,
        clip_scene: SceneSpec,
        clip: GeneratedClip,
    ) -> None:
        """clip(clip_ir 의 clip_scene 조건으로 만든 것)에서, 아직 쓸 기준이 없는 객체의 기준 이미지를 뽑는다."""
        if self._extractor is None or not ctx.ir.has_scene(clip_scene.scene_key):
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
            images = self._extractor.extract(request)
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
