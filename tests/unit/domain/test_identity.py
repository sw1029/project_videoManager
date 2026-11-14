"""정체성 기준 이미지 규칙 (ADR-0015): 어떤 기준을 어느 장면에 넘기고, 어떤 객체에 새 기준이 필요한가.

US-03 AC-31 "장면 전환 후에도 객체의 식별자·연결 정보 유지" 를 생성 조건 쪽에서 받치는 규칙이다.
"""

from dataclasses import replace

import pytest

from frame.domain.enums import ChangeScope, EntityKind, ReferenceOrigin
from frame.domain.ir import EntitySpec, IntermediateRepresentation, RequirementSpec, SceneSpec
from frame.domain.models import ReferenceImage
from frame.domain.revision import AttributeChange, DefaultPropagationPolicy
from frame.domain.services.identity import (
    IdentityPolicy,
    appearance_attributes,
    entities_needing_identity,
    entity_appearance_fingerprint,
    identity_references_for,
    is_valid_identity,
    same_appearance,
)

policy = IdentityPolicy()
propagation = DefaultPropagationPolicy()


def anchor(ir, entity_key, scene_key, *, source_fingerprint="fp-src", uri=None, ref_id=None):
    """scene_key 장면의 클립에서 뽑은 entity_key 의 정체성 기준."""
    return ReferenceImage(
        reference_image_id=ref_id or f"img-{entity_key}-{scene_key}",
        version_id="ver-1",
        image_uri=uri or f"mem://identity/{scene_key}/{entity_key}.png",
        entity_key=entity_key,
        origin=ReferenceOrigin.DERIVED,
        source_scene_key=scene_key,
        source_fingerprint=source_fingerprint,
        appearance_fingerprint=entity_appearance_fingerprint(ir, scene_key, entity_key),
    )


def user_ref(entity_key, uri="mem://refs/user.png"):
    return ReferenceImage("img-user", "ver-1", uri, entity_key)


def keys(refs):
    return [r.entity_key for r in refs]


# ---- 고정 속성과 외형 fingerprint ------------------------------------------------------


def test_appearance_attributes_exclude_relations_and_transient_predicates():
    scene = SceneSpec(
        "s",
        0,
        4000,
        "",
        ("barista", "cup"),
        (
            RequirementSpec("s", "outfit", "white shirt", "barista"),
            RequirementSpec("s", "action", "waves hand", "barista"),
            RequirementSpec("s", "pose", "standing", "barista"),
            RequirementSpec("s", "expression", "smile", "barista"),
            RequirementSpec("s", "position", "left", "barista"),
            RequirementSpec("s", "holds", "cup", "barista", "cup"),
            RequirementSpec("s", "color", "red", "cup"),
        ),
    )
    assert appearance_attributes(scene, "barista") == {"outfit": "white shirt"}
    assert appearance_attributes(scene, "cup") == {"color": "red"}


def test_appearance_fingerprint_ignores_actions_but_follows_fixed_attributes(ir):
    base = entity_appearance_fingerprint(ir, "intro", "barista")
    acted = propagation.apply(ir, AttributeChange("barista", "action", "waves")).ir
    recolored = propagation.apply(ir, AttributeChange("barista", "shirt_color", "black")).ir

    assert entity_appearance_fingerprint(acted, "intro", "barista") == base
    assert entity_appearance_fingerprint(recolored, "intro", "barista") != base


def test_appearance_fingerprint_follows_the_entity_definition(ir):
    renamed = replace(
        ir,
        entities=tuple(
            replace(e, description="검은 앞치마의 바리스타") if e.entity_key == "barista" else e
            for e in ir.entities
        ),
    )
    assert entity_appearance_fingerprint(renamed, "intro", "barista") != (
        entity_appearance_fingerprint(ir, "intro", "barista")
    )


# ---- 기준의 유효성 ----------------------------------------------------------------------


def test_anchor_stays_valid_while_its_source_scene_keeps_the_entity_appearance(ir):
    ref = anchor(ir, "barista", "intro")
    cup_recolored = propagation.apply(ir, AttributeChange("cup", "color", "teal")).ir

    assert is_valid_identity(ir, ref)
    assert is_valid_identity(cup_recolored, ref)  # 다른 객체(컵)가 바뀌어도 바리스타 기준은 유효


def test_anchor_is_invalid_once_the_entity_appearance_changes_in_its_source_scene(ir):
    ref = anchor(ir, "cup", "intro")
    recolored = propagation.apply(ir, AttributeChange("cup", "color", "teal")).ir

    assert not is_valid_identity(recolored, ref)


def test_anchor_is_invalid_when_its_source_scene_or_entity_is_gone(ir):
    ref = anchor(ir, "barista", "intro")
    without_scene = IntermediateRepresentation(
        entities=ir.entities,
        scenes=(replace(ir.scene("closeup"), planned_start_ms=0, planned_end_ms=4000),),
    )
    without_entity = ir.with_scene(
        replace(ir.scene("intro"), entity_keys=("cup", "cafe"), requirements=())
    )

    assert not is_valid_identity(without_scene, ref)
    assert not is_valid_identity(without_entity, ref)


def test_user_references_are_not_identity_anchors(ir):
    assert not is_valid_identity(ir, user_ref("barista"))


# ---- 장면에 넘길 기준 고르기 -------------------------------------------------------------


@pytest.mark.ac("AC-31")
def test_scene_gets_the_anchors_of_its_entities_from_other_scenes(ir):
    refs = (anchor(ir, "barista", "intro"), anchor(ir, "cup", "intro"))

    assert keys(identity_references_for(ir, ir.scene("outro"), refs, policy)) == ["barista"]
    assert keys(identity_references_for(ir, ir.scene("closeup"), refs, policy)) == ["cup"]


def test_anchor_is_not_applied_to_the_very_clip_it_came_from(ir):
    """기준을 뽑은 장면이 그때와 같은 조건이면 그 기준을 그 장면에 다시 넘기지 않는다 (같은 요청 = 같은 캐시 키)."""
    refs = (anchor(ir, "barista", "intro", source_fingerprint="fp-intro"),)

    same = identity_references_for(
        ir, ir.scene("intro"), refs, policy, scene_fingerprint="fp-intro"
    )
    changed = identity_references_for(
        ir, ir.scene("intro"), refs, policy, scene_fingerprint="fp-intro-changed"
    )

    assert same == ()
    assert keys(changed) == ["barista"]  # 장면 조건이 바뀌어 다시 만들 때는 정체성을 이어 간다


def test_invalid_anchors_are_dropped(ir):
    refs = (anchor(ir, "barista", "intro"), anchor(ir, "cup", "intro"))
    recolored = propagation.apply(ir, AttributeChange("cup", "color", "teal")).ir

    chosen = identity_references_for(recolored, recolored.scene("closeup"), refs, policy)

    assert chosen == ()  # 빨간 컵 기준은 청록 컵 장면에 넘기지 않는다


def test_anchor_conflicting_with_the_target_scene_attributes_is_not_applied(ir):
    """기준 장면에서는 빨강, 이 장면에서만 파랑이면 (장면 한정 수정) 기준이 조건과 어긋난다."""
    ref = anchor(ir, "cup", "intro")
    only_closeup = propagation.apply(
        ir, AttributeChange("cup", "color", "blue", ChangeScope.SCENES, ("closeup",))
    ).ir

    assert is_valid_identity(only_closeup, ref)  # 기준 장면(intro)의 컵은 그대로다
    assert (
        identity_references_for(only_closeup, only_closeup.scene("closeup"), (ref,), policy) == ()
    )


def test_extra_attributes_in_the_target_scene_do_not_conflict(ir):
    ref = anchor(ir, "cup", "intro")
    with_text = propagation.apply(
        ir, AttributeChange("cup", "text", "FRAME", ChangeScope.SCENES, ("closeup",))
    ).ir

    assert keys(identity_references_for(with_text, with_text.scene("closeup"), (ref,), policy)) == [
        "cup"
    ]


def test_differing_actions_do_not_conflict():
    ir = IntermediateRepresentation(
        entities=(EntitySpec("barista", EntityKind.PERSON, "바리스타"),),
        scenes=(
            SceneSpec(
                "a",
                0,
                4000,
                "",
                ("barista",),
                (RequirementSpec("a", "action", "shows cup", "barista"),),
            ),
            SceneSpec(
                "b",
                4000,
                8000,
                "",
                ("barista",),
                (RequirementSpec("b", "action", "waves hand", "barista"),),
            ),
        ),
    )
    refs = (anchor(ir, "barista", "a"),)

    assert keys(identity_references_for(ir, ir.scene("b"), refs, policy)) == ["barista"]


def test_user_reference_takes_precedence_over_a_derived_anchor(ir):
    refs = (user_ref("barista"), anchor(ir, "barista", "intro"))

    assert identity_references_for(ir, ir.scene("outro"), refs, policy) == ()


def test_kinds_outside_the_policy_get_no_anchor(ir):
    refs = (anchor(ir, "cafe", "intro"), anchor(ir, "barista", "intro"))

    assert keys(identity_references_for(ir, ir.scene("outro"), refs, policy)) == ["barista"]
    with_background = IdentityPolicy(kinds=(EntityKind.PERSON, EntityKind.BACKGROUND))
    assert keys(identity_references_for(ir, ir.scene("outro"), refs, with_background)) == [
        "barista",
        "cafe",
    ]


def test_anchors_are_ordered_by_kind_priority_and_capped(ir):
    crowded = ir.with_scene(
        replace(ir.scene("outro"), entity_keys=("cafe", "cup", "barista"), requirements=())
    )
    refs = (
        anchor(crowded, "cup", "intro"),
        anchor(crowded, "barista", "intro"),
    )

    one = IdentityPolicy(max_references=1)

    assert keys(identity_references_for(crowded, crowded.scene("outro"), refs, policy)) == [
        "barista",
        "cup",
    ]
    assert keys(identity_references_for(crowded, crowded.scene("outro"), refs, one)) == ["barista"]


def test_one_anchor_per_entity_is_chosen_deterministically(ir):
    first = anchor(ir, "barista", "intro", uri="mem://identity/a.png", ref_id="img-a")
    second = anchor(ir, "barista", "intro", uri="mem://identity/b.png", ref_id="img-b")

    for refs in ((first, second), (second, first)):
        chosen = identity_references_for(ir, ir.scene("outro"), refs, policy)
        assert [r.image_uri for r in chosen] == ["mem://identity/a.png"]


def test_policy_with_zero_references_disables_anchors(ir):
    refs = (anchor(ir, "barista", "intro"),)
    assert (
        identity_references_for(ir, ir.scene("outro"), refs, IdentityPolicy(max_references=0)) == ()
    )


# ---- 새 기준이 필요한 객체 ---------------------------------------------------------------


def test_entities_without_a_valid_anchor_need_one(ir):
    assert entities_needing_identity(ir, ir.scene("intro"), (), policy) == ("barista", "cup")
    refs = (anchor(ir, "barista", "intro"),)
    assert entities_needing_identity(ir, ir.scene("intro"), refs, policy) == ("cup",)
    assert entities_needing_identity(ir, ir.scene("outro"), refs, policy) == ()


def test_entities_with_a_user_reference_need_no_anchor(ir):
    assert entities_needing_identity(ir, ir.scene("intro"), (user_ref("barista"),), policy) == (
        "cup",
    )


def test_entity_needs_a_new_anchor_after_its_old_one_became_invalid(ir):
    refs = (anchor(ir, "cup", "intro"),)
    recolored = propagation.apply(ir, AttributeChange("cup", "color", "teal")).ir

    assert "cup" in entities_needing_identity(recolored, recolored.scene("intro"), refs, policy)


def test_same_appearance_compares_one_entity_across_two_irs(ir):
    recolored = propagation.apply(ir, AttributeChange("cup", "color", "teal")).ir

    assert same_appearance(ir, recolored, "intro", "barista")
    assert not same_appearance(ir, recolored, "intro", "cup")
    assert not same_appearance(ir, recolored, "missing", "cup")
