"""수정 의도 (ADR-0017): 수정의 종류(CHANGE / REPAIR)와 '그대로 둘 요소'.

컨셉 화면의 '컵 01 수정' 패널: ① 적용 범위 ② 변경 요청 ③ 유지할 요소(인물·동작 / 배경·조명 / 컵 형태) ④ 실행.
"""

import pytest

from frame.domain.enums import ChangeScope, KeepAspect, RevisionKind
from frame.domain.errors import NotFound, ValidationError
from frame.domain.revision import (
    KeepItem,
    RevisionIntent,
    next_seed,
    normalize_keep,
    repair_targets,
)
from frame.domain.services.propagation import AttributeChange


def test_entity_aspects_need_an_entity_and_scene_aspects_must_not_have_one():
    assert KeepItem(KeepAspect.APPEARANCE, "barista").entity_key == "barista"
    assert KeepItem(KeepAspect.LIGHTING).is_scene_level
    assert KeepItem("SHAPE", "cup").aspect is KeepAspect.SHAPE  # 문자열도 받는다
    with pytest.raises(ValidationError):
        KeepItem(KeepAspect.ACTION)
    with pytest.raises(ValidationError):
        KeepItem(KeepAspect.BACKGROUND, "cafe")


def test_keep_items_apply_to_scenes_where_their_entity_appears(ir):
    person, light = KeepItem(KeepAspect.ACTION, "barista"), KeepItem(KeepAspect.LIGHTING)
    intent = RevisionIntent(
        RevisionKind.CHANGE, "color", "teal", entity_key="cup", keep=(person, light)
    )

    assert intent.keep_for(ir.scene("intro")) == (person, light)
    assert intent.keep_for(ir.scene("closeup")) == (light,)  # 바리스타가 없는 장면


def test_intent_round_trips_through_a_plain_dict():
    intent = RevisionIntent(
        RevisionKind.REPAIR,
        "color",
        "teal",
        entity_key="cup",
        scope=ChangeScope.SCENES,
        target_scene_keys=("intro", "closeup"),
        keep=(KeepItem(KeepAspect.APPEARANCE, "barista"), KeepItem(KeepAspect.BACKGROUND)),
        request_text="컵 색상만 청록색으로 동일해줘. 인물과 배경은 유지해줘.",
    )

    assert RevisionIntent.from_dict(intent.to_dict()) == intent
    assert intent.to_dict()["keep"][0] == {"aspect": "APPEARANCE", "entity_key": "barista"}


# ---- 유지할 요소의 검증 ----------------------------------------------------------------


def test_keep_items_are_checked_against_the_entities_and_deduplicated(ir):
    change = AttributeChange("cup", "color", "teal")
    person = KeepItem(KeepAspect.APPEARANCE, "barista")

    assert normalize_keep(ir, (person, KeepItem(KeepAspect.LIGHTING), person), change) == (
        person,
        KeepItem(KeepAspect.LIGHTING),
    )
    with pytest.raises(NotFound):
        normalize_keep(ir, (KeepItem(KeepAspect.ACTION, "ghost"),), change)


@pytest.mark.parametrize(
    ("change", "keep"),
    [
        # 색을 바꾸면서 그 객체의 모습을 그대로 두라고 할 수는 없다
        (("cup", "color", "teal"), KeepItem(KeepAspect.APPEARANCE, "cup")),
        (("cup", "shape", "tall"), KeepItem(KeepAspect.SHAPE, "cup")),
        (("barista", "action", "waves hand"), KeepItem(KeepAspect.ACTION, "barista")),
        ((None, "lighting", "cold"), KeepItem(KeepAspect.LIGHTING)),
        ((None, "location", "street"), KeepItem(KeepAspect.BACKGROUND)),
    ],
)
def test_keeping_what_the_change_itself_alters_is_contradictory(ir, change, keep):
    with pytest.raises(ValidationError, match="유지"):
        normalize_keep(ir, (keep,), AttributeChange(*change))


def test_the_changed_entity_can_still_keep_its_other_aspects(ir):
    """컨셉의 '컵 형태' 체크: 색을 바꾸되 형태는 그대로."""
    change = AttributeChange("cup", "color", "teal")
    keep = (KeepItem(KeepAspect.SHAPE, "cup"), KeepItem(KeepAspect.ACTION, "barista"))
    assert normalize_keep(ir, keep, change) == keep


# ---- REPAIR: 요구조건은 그대로 두고 다시 만들 장면 -------------------------------------------


def test_repair_targets_are_the_scenes_that_carry_the_requirement(ir):
    assert repair_targets(ir, AttributeChange("cup", "color", "red")) == ("intro", "closeup")
    scoped = AttributeChange("cup", "color", "red", ChangeScope.SCENES, ("closeup",))
    assert repair_targets(ir, scoped) == ("closeup",)


def test_repair_does_not_change_values(ir):
    with pytest.raises(ValidationError, match="CHANGE"):
        repair_targets(ir, AttributeChange("cup", "color", "teal"))


def test_repair_needs_the_requirement_to_exist(ir):
    with pytest.raises(ValidationError):
        repair_targets(ir, AttributeChange("cup", "texture", "matte"))
    # 장면을 짚었는데 그 장면에 그 요구조건이 없으면 오류다 (모든 등장 장면이면 그 장면만 뺀다)
    with pytest.raises(ValidationError):
        repair_targets(
            ir, AttributeChange("barista", "holds", "cup", ChangeScope.SCENES, ("outro",))
        )
    assert repair_targets(ir, AttributeChange("barista", "holds", "cup")) == ("intro",)


def test_repair_works_for_a_background_entity(ir):
    assert repair_targets(ir, AttributeChange("cafe", "lighting", "warm")) == (
        "intro",
        "closeup",
        "outro",
    )


def test_next_seed_differs_from_the_current_one():
    assert next_seed(41) == 42
    assert next_seed(None) == 1
