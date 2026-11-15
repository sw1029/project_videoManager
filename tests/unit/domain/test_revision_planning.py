"""수정 요청 풀기 (resolve_revision, ADR-0017·0019): 수정은 중간표현의 규칙을 깨뜨리지 않는다 (감사 A-20).

중간표현의 규칙은 IntermediateRepresentation.validate() 한 곳에 있다. 구조화 결과는 그 검사를 거치는데 수정 전파의
결과는 거치지 않아서, 구조화에서는 거부되는 중간표현이 수정으로는 만들어졌다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from frame.domain.enums import ChangeScope, RevisionKind
from frame.domain.errors import ValidationError
from frame.domain.ir import RequirementSpec
from frame.domain.revision import AttributeChange, DefaultPropagationPolicy
from frame.domain.revision.resolution import resolve_revision

policy = DefaultPropagationPolicy()


def test_a_background_cannot_become_the_subject_of_a_relation(ir):
    """배경 객체는 관계의 주체가 될 수 없다 (ADR-0008). 구조화에서 거부되는 것은 수정으로도 만들 수 없다."""
    change = AttributeChange("cafe", "holds", "cup", ChangeScope.SCENES, ("intro",))

    with pytest.raises(ValidationError, match="배경"):
        resolve_revision(ir, change, policy)


def test_a_change_that_keeps_the_rules_goes_through(ir):
    resolved = resolve_revision(ir, AttributeChange("cup", "color", "teal"), policy)

    assert resolved.kind is RevisionKind.CHANGE
    assert resolved.target_scene_keys == ("intro", "closeup")
    resolved.ir.validate()


def test_an_ir_that_was_already_out_of_rule_can_still_be_revised(ir):
    """규칙이 생기기 전에 저장된 중간표현. 이 수정이 만든 문제가 아니면 수정을 막지 않는다."""
    intro = ir.scene("intro")
    legacy = replace(
        ir,
        scenes=(
            replace(
                intro,
                requirements=(
                    *intro.requirements,
                    RequirementSpec("intro", "holds", "cup", "cafe", related_entity_key="cup"),
                ),
            ),
            *ir.scenes[1:],
        ),
    )
    with pytest.raises(ValidationError):
        legacy.validate()

    resolved = resolve_revision(legacy, AttributeChange("cup", "color", "teal"), policy)

    assert resolved.kind is RevisionKind.CHANGE and "intro" in resolved.target_scene_keys
