"""정체성 기준 이미지 규칙 (US-03, ADR-0015).

장면마다 따로 생성하면 같은 객체(인물·소품)가 장면마다 다르게 나온다. 객체가 처음 나온 장면의 클립에서
그 객체의 기준 이미지를 뽑아 두고(ReferenceImage.origin = DERIVED), 그 객체가 나오는 다른 장면을 생성할 때
참조 이미지로 넘겨 같은 모습을 유지한다. 이 모듈은 그 규칙만 다룬다 (이미지를 뽑고 보내는 일은 어댑터).

규칙
- 고정 속성: 객체의 속성 조건 중 관계 술어와 장면마다 달라지는 술어(행동·자세·표정·위치)를 뺀 것.
  외형 fingerprint = 객체 정의(종류·설명) + 그 장면에서의 고정 속성.
- 유효성: 기준을 뽑은 장면에서 그 객체의 외형 fingerprint 가 뽑을 때와 같아야 한다. 수정으로 외형이 바뀌면
  (컵 색 변경 등) 옛 기준은 쓰지 않고 새 클립에서 다시 뽑는다. 수정한 값이 기준 이미지와 어긋나지 않게 하려는 것이다.
- 충돌: 기준 장면과 넘길 장면이 같은 고정 속성에 다른 값을 요구하면(장면 한정 수정) 그 장면에는 넘기지 않는다.
- 자기 자신: 기준을 뽑아낸 바로 그 클립(같은 장면·같은 조건)을 만드는 요청에는 넘기지 않는다. 같은 요청은
  같은 생성 키를 가져야 캐시·작업 원장이 맞는다. 장면 조건이 바뀌어 다시 만들 때는 넘겨서 정체성을 이어 간다.
- 사용자 참조 이미지가 있는 객체는 그 이미지가 기준이다 (새로 뽑지 않고, 뽑아 둔 것도 넘기지 않는다).
- 한 요청에 넘기는 기준은 정책의 종류 우선순위(인물 -> 동물 -> 소품 …) 순서로 max_references 개까지다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..enums import EntityKind, ReferenceOrigin
from ..hashing import canonical_hash
from ..ir import IntermediateRepresentation, SceneSpec
from ..models import ReferenceImage
from ..vocabulary import DEFAULT_VOCABULARY

# 기준 이미지를 두는 객체 종류와 우선순위. 배경·문구는 기본으로 빼 둔다
# (배경은 화면 전체라 구도가 그대로 복사되기 쉽고, 문구는 프롬프트의 철자로 다룬다).
DEFAULT_IDENTITY_KINDS: tuple[EntityKind, ...] = (
    EntityKind.PERSON,
    EntityKind.ANIMAL,
    EntityKind.PROP,
    EntityKind.OTHER,
)


@dataclass(frozen=True)
class IdentityPolicy:
    kinds: tuple[EntityKind, ...] = DEFAULT_IDENTITY_KINDS  # 우선순위 순
    max_references: int = 3  # 장면 요청 하나에 넘기는 정체성 기준의 최대 개수

    def priority(self, kind: EntityKind) -> int | None:
        return self.kinds.index(kind) if kind in self.kinds else None


def appearance_attributes(scene: SceneSpec, entity_key: str) -> dict[str, str]:
    """그 장면에서 객체에 지정된 고정 속성 (술어 -> 값). 관계·일시적 술어는 뺀다."""
    return {
        r.predicate: r.expected_value
        for r in scene.requirements
        if r.entity_key == entity_key
        and not r.is_relational
        and not DEFAULT_VOCABULARY.is_transient(r.predicate)
    }


def entity_appearance_fingerprint(
    ir: IntermediateRepresentation, scene_key: str, entity_key: str
) -> str:
    """장면 scene_key 에서 객체 entity_key 가 어떻게 보여야 하는지의 해시 (객체 정의 + 고정 속성)."""
    entity = ir.entity(entity_key)
    attributes = appearance_attributes(ir.scene(scene_key), entity_key)
    return canonical_hash(
        {
            "entity": {
                "entity_key": entity.entity_key,
                "kind": str(entity.kind),
                "description": entity.description,
            },
            "attributes": sorted(attributes.items()),
        }
    )


def same_appearance(
    before: IntermediateRepresentation,
    after: IntermediateRepresentation,
    scene_key: str,
    entity_key: str,
) -> bool:
    """두 IR 의 같은 장면에서 객체의 외형 조건이 같은가 (한쪽에 장면·객체가 없으면 거짓)."""
    for ir in (before, after):
        if not ir.has_scene(scene_key) or entity_key not in ir.scene(scene_key).entity_keys:
            return False
    return entity_appearance_fingerprint(before, scene_key, entity_key) == (
        entity_appearance_fingerprint(after, scene_key, entity_key)
    )


def is_valid_identity(ir: IntermediateRepresentation, ref: ReferenceImage) -> bool:
    """뽑아 둔 기준이 이 IR 에서도 쓸 수 있는가: 기준 장면에서 객체의 외형 조건이 뽑을 때 그대로여야 한다."""
    if ref.origin is not ReferenceOrigin.DERIVED or ref.entity_key is None:
        return False
    if ref.source_scene_key is None or not ir.has_scene(ref.source_scene_key):
        return False
    if ref.entity_key not in ir.scene(ref.source_scene_key).entity_keys:
        return False
    current = entity_appearance_fingerprint(ir, ref.source_scene_key, ref.entity_key)
    return current == ref.appearance_fingerprint


def identity_references_for(
    ir: IntermediateRepresentation,
    scene: SceneSpec,
    references: Sequence[ReferenceImage],
    policy: IdentityPolicy,
    *,
    scene_fingerprint: str | None = None,
) -> tuple[ReferenceImage, ...]:
    """이 장면을 생성할 때 넘길 정체성 기준 (객체마다 하나, 우선순위 순, 정책 상한까지).

    scene_fingerprint 는 이 장면 요청의 조건 fingerprint 다. 같은 장면·같은 조건의 클립에서 뽑은 기준은 뺀다.
    """
    if policy.max_references <= 0:
        return ()
    user_keys = _user_entity_keys(references)
    scene_order = {s.scene_key: i for i, s in enumerate(ir.scenes)}
    chosen: list[tuple[tuple[int, int], ReferenceImage]] = []
    for position, entity_key in enumerate(scene.entity_keys):
        priority = policy.priority(ir.entity(entity_key).kind)
        if priority is None or entity_key in user_keys:
            continue
        candidates = [
            ref
            for ref in references
            if ref.entity_key == entity_key
            and is_valid_identity(ir, ref)
            and not _conflicts(ir, ref, scene)
            and not _is_own_clip(ref, scene, scene_fingerprint)
        ]
        if not candidates:
            continue
        best = min(
            candidates,
            key=lambda r: (
                scene_order.get(r.source_scene_key or "", len(scene_order)),
                r.image_uri,
            ),
        )
        chosen.append(((priority, position), best))
    chosen.sort(key=lambda item: item[0])
    return tuple(ref for _, ref in chosen[: policy.max_references])


def entities_needing_identity(
    ir: IntermediateRepresentation,
    scene: SceneSpec,
    references: Sequence[ReferenceImage],
    policy: IdentityPolicy,
) -> tuple[str, ...]:
    """이 장면에 나오는 객체 중 쓸 수 있는 기준이 없는 것 (정책 종류만, 우선순위 순)."""
    user_keys = _user_entity_keys(references)
    valid_keys = {r.entity_key for r in references if is_valid_identity(ir, r)}
    needing: list[tuple[tuple[int, int], str]] = []
    for position, entity_key in enumerate(scene.entity_keys):
        priority = policy.priority(ir.entity(entity_key).kind)
        if priority is None or entity_key in user_keys or entity_key in valid_keys:
            continue
        needing.append(((priority, position), entity_key))
    needing.sort(key=lambda item: item[0])
    return tuple(key for _, key in needing)


def _user_entity_keys(references: Sequence[ReferenceImage]) -> set[str]:
    return {
        r.entity_key
        for r in references
        if r.origin is ReferenceOrigin.USER and r.entity_key is not None
    }


def _conflicts(ir: IntermediateRepresentation, ref: ReferenceImage, scene: SceneSpec) -> bool:
    """기준 장면과 대상 장면이 같은 고정 속성에 다른 값을 요구하는가."""
    assert ref.entity_key is not None and ref.source_scene_key is not None
    source = appearance_attributes(ir.scene(ref.source_scene_key), ref.entity_key)
    target = appearance_attributes(scene, ref.entity_key)
    return any(source.get(predicate, value) != value for predicate, value in target.items())


def _is_own_clip(ref: ReferenceImage, scene: SceneSpec, scene_fingerprint: str | None) -> bool:
    return (
        scene_fingerprint is not None
        and ref.source_scene_key == scene.scene_key
        and ref.source_fingerprint == scene_fingerprint
    )
