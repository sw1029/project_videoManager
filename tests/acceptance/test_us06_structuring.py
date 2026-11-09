"""US-06 요구사항 구조화 및 반영 확인 — AC-11 ~ AC-14 를 유스케이스 수준에서 재현한다.

(실제 모델 어댑터로 바꿔 돌릴 때도 같은 시나리오를 쓴다: 컨테이너 설정만 바뀐다.)
"""

import pytest

from frame.application.dto import CreateRevisionCommand
from frame.domain.enums import CheckType, Verdict
from frame.domain.services.propagation import AttributeChange
from tests.support.factories import generate, version_of


@pytest.mark.ac("AC-11")
def test_ac11_prompt_is_decomposed_into_entities_attributes_scenes_and_time(container, draft):
    ir = version_of(container, draft.version_id).ir
    assert {e.entity_key: str(e.kind) for e in ir.entities} == {
        "barista": "PERSON",
        "cup": "PROP",
        "cafe": "BACKGROUND",
    }
    assert [(s.scene_key, s.planned_start_ms, s.planned_end_ms) for s in ir.scenes] == [
        ("intro", 0, 4000),
        ("closeup", 4000, 8000),
        ("outro", 8000, 12000),
    ]
    assert {
        (r.scene_key, r.entity_key, r.predicate, r.expected_value) for r in ir.iter_requirements()
    } >= {
        ("intro", "cup", "color", "red"),
        ("intro", "barista", "holds", "cup"),
        ("outro", "cafe", "lighting", "warm"),
    }


@pytest.mark.ac("AC-12")
def test_ac12_each_requirement_gets_a_mapped_verdict(container, draft):
    generate(container, draft.version_id)
    view = container.queries.get_checks(draft.version_id)
    req_results = {
        r.requirement_key: r.verdict for r in view.results if r.check_type is CheckType.REQUIREMENT
    }
    expected_keys = {r.key for r in version_of(container, draft.version_id).ir.iter_requirements()}
    assert set(req_results) == expected_keys
    assert set(req_results.values()) <= {Verdict.PASS, Verdict.FAIL, Verdict.UNDETERMINED}


@pytest.mark.ac("AC-13")
def test_ac13_attribute_block_change_feeds_generation_without_rewriting_prompt(container, draft):
    generate(container, draft.version_id)
    revision = container.create_revision.execute(
        CreateRevisionCommand(draft.version_id, AttributeChange("cup", "color", "teal"))
    )
    revised = generate(container, revision.version_id)
    assert revised.source_prompt == version_of(container, draft.version_id).source_prompt
    regenerated_conditions = container.artifact_store.get(revised.clips["closeup"].uri).decode(
        "utf-8"
    )
    assert '"expected_value": "teal"' in regenerated_conditions


@pytest.mark.ac("AC-14")
def test_ac14_overall_reflection_rate_reaches_80_percent(container, draft):
    generate(container, draft.version_id)
    summary = container.queries.get_checks(draft.version_id).summary
    assert summary.requirement_reflection_rate is not None
    assert summary.requirement_reflection_rate >= 0.8
