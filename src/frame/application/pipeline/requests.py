"""장면 생성 요청을 만드는 규칙.

생성 단계(GenerateStage)와 변경 계획 미리보기(PlanRevision)가 함께 쓴다. 계획이 알려 준 방식·비용이 실제로 만들 때와
어긋나지 않으려면 둘이 같은 요청을 봐야 한다.
"""

from __future__ import annotations

from collections.abc import Sequence

from frame.domain.enums import ReferenceOrigin
from frame.domain.ir import IntermediateRepresentation, SceneSpec
from frame.domain.models import ReferenceImage, VideoVersion
from frame.domain.revision import RevisionIntent

from ..ports.generator import ParentClip, RevisionDirective, SceneGenerationRequest
from ..ports.structurer import ReferenceImageInput


def condition_request(
    version: VideoVersion,
    ir: IntermediateRepresentation,
    scene: SceneSpec,
    references: Sequence[ReferenceImage],
) -> SceneGenerationRequest:
    """조건만 담은 요청: 장면·객체·사용자 참조·seed. 재사용 판단에 쓰는 fingerprint 가 여기서 나온다."""
    return SceneGenerationRequest(
        version_id=version.version_id,
        scene=scene,
        entities=tuple(ir.entity(key) for key in scene.entity_keys),
        references=tuple(
            ReferenceImageInput(image_uri=r.image_uri, entity_key=r.entity_key)
            for r in references
            if r.origin is ReferenceOrigin.USER
            and (r.entity_key is None or r.entity_key in scene.entity_keys)
        ),
        seed=version.seed_for(scene.scene_key),
    )


def revision_directive(intent: RevisionIntent | None, scene: SceneSpec) -> RevisionDirective | None:
    """수정 버전이 이 장면을 다시 만들기로 했으면 그 지시 (ADR-0017). 그 밖의 장면·원본 버전은 None."""
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


def parent_clip_of(parent: VideoVersion | None, scene: SceneSpec) -> ParentClip | None:
    """부모 버전에 같은 장면의 클립이 있으면 그 클립과 부모 쪽 장면 조건 (ADR-0016)."""
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
