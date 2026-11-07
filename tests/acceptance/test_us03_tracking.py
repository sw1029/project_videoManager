"""US-03 객체 추적 및 속성 일관성 검수 — AC-31 ~ AC-34."""

import pytest

from frame.adapters.checker import FakeConsistencyChecker, FakeRequirementChecker
from frame.domain.checks import WarningInterval
from frame.domain.enums import CheckType, TrackingStatus, Verdict
from tests.support.factories import create_draft, generate, make_container


@pytest.mark.ac("AC-31")
def test_ac31_entity_identity_is_kept_across_scenes(container, draft):
    generate(container, draft.version_id)
    records = container.queries.get_entity_tracks(draft.version_id, "cup").records
    assert [r.scene_key for r in records] == ["intro", "closeup"]
    assert {r.entity_key for r in records} == {"cup"}
    timeline = [(s.start_ms, s.end_ms) for r in records for s in r.segments]
    assert timeline == [(0, 4000), (4000, 8000)]  # 장면 전환 후에도 같은 식별자로 이어진다


@pytest.mark.ac("AC-32")
def test_ac32_fixed_attribute_consistency_rate_is_at_least_90_percent(container, draft):
    generate(container, draft.version_id)
    summary = container.queries.get_checks(draft.version_id).summary
    assert summary.consistency_rate is not None and summary.consistency_rate >= 0.9


@pytest.mark.ac("AC-33")
def test_ac33_broken_consistency_marks_the_exact_interval():
    warning = WarningInterval("closeup", 5000, 6000, "컵 색상 불일치")
    container = make_container(
        checkers=(FakeRequirementChecker(), FakeConsistencyChecker({"cup": [warning]}))
    )
    draft = create_draft(container)
    generate(container, draft.version_id)

    results = container.queries.get_checks(draft.version_id).results
    cup = next(
        r for r in results if r.check_type is CheckType.CONSISTENCY and r.entity_key == "cup"
    )
    assert cup.warnings == (warning,)
    assert cup.score == pytest.approx(1 - 1000 / 8000)  # 등장 8초 중 1초 경고
    assert cup.verdict is Verdict.FAIL  # 0.875 < 0.9


@pytest.mark.ac("AC-34")
def test_ac34_track_boxes_provide_a_mask_region_per_timestamp(container, draft):
    generate(container, draft.version_id)
    records = container.queries.get_entity_tracks(draft.version_id, "cup").records
    assert all(r.status is TrackingStatus.COMPLETED for r in records)
    boxes = [b for r in records for s in r.segments for b in s.boxes]
    assert boxes and all(0 < b.w <= 1 and 0 < b.h <= 1 for b in boxes)
    assert sorted(b.t_ms for b in boxes) == list(range(0, 8000, 1000))
