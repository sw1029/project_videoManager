"""US-01 선택 객체·조건 수정 및 재생성 — AC-01, AC-02 와 지표 1 평가 하네스 연결."""

import pytest

from frame.application.dto import CreateRevisionCommand
from frame.domain.enums import CheckType, VersionState
from frame.domain.services.propagation import AttributeChange
from frame.evaluation import EditCase, evaluate_cases
from tests.support.factories import generate, version_of


def _revise_and_generate(container, version_id, entity="cup", predicate="color", value="teal"):
    revision = container.create_revision.execute(
        CreateRevisionCommand(version_id, AttributeChange(entity, predicate, value))
    )
    return revision, generate(container, revision.version_id)


@pytest.mark.ac("AC-01")
def test_ac01_only_target_attribute_changes_and_non_targets_are_preserved(container, draft):
    original = generate(container, draft.version_id)
    revision, revised = _revise_and_generate(container, draft.version_id)

    # 대상 속성은 바뀌고
    assert {r.expected_value for r in revised.ir.iter_requirements() if r.predicate == "color"} == {
        "teal"
    }
    # 비대상 조건(다른 객체·속성)은 그대로
    others_before = {
        r.key: r.expected_value for r in original.ir.iter_requirements() if r.predicate != "color"
    }
    others_after = {
        r.key: r.expected_value for r in revised.ir.iter_requirements() if r.predicate != "color"
    }
    assert others_before == others_after
    # 비대상 보존율 >= 90%
    assert container.queries.get_checks(revision.version_id).summary.preservation_rate >= 0.9
    # 원본은 그대로 남는다
    assert version_of(container, draft.version_id) == original


@pytest.mark.ac("AC-02")
def test_ac02_change_is_propagated_to_all_scenes_with_the_entity(container, draft):
    generate(container, draft.version_id)
    revision, revised = _revise_and_generate(container, draft.version_id)
    assert set(revision.changed_scene_keys) == {
        s.scene_key for s in revised.ir.scenes_with_entity("cup")
    }
    assert revised.state is VersionState.COMPLETED


def test_metric_1_harness_over_repeated_single_attribute_edits(container, draft):
    """지표 1: 단일 속성 수정 N 건 -> 성공률 >= 80%, 비대상 변경률 <= 10% (Fake 로 하네스 동작만 검증)."""
    generate(container, draft.version_id)
    cases = []
    for i, (entity, predicate, value) in enumerate(
        [
            ("cup", "color", "teal"),
            ("barista", "shirt_color", "black"),
            ("cafe", "lighting", "cool"),
        ]
    ):
        revision, _ = _revise_and_generate(container, draft.version_id, entity, predicate, value)
        results = container.queries.get_checks(revision.version_id).results
        cases.append(
            EditCase(
                f"case_{i}", entity, predicate, frozenset(revision.changed_scene_keys), results
            )
        )
    evaluation = evaluate_cases(cases)
    assert evaluation.cases == 3
    assert evaluation.success_rate >= 0.8
    assert evaluation.mean_non_target_change_rate <= 0.1
    assert evaluation.meets_target
    assert all(
        r.check_type is not CheckType.PRESERVATION or r.score is not None
        for c in cases
        for r in c.revision_results
    )
