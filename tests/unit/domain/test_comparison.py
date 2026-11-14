"""두 버전 비교 (US-04 AC-43 전후 비교의 도메인 규칙)."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from frame.domain.artifacts import GeneratedClip
from frame.domain.enums import VersionState
from frame.domain.ir import EntitySpec, RequirementSpec, SceneSpec
from frame.domain.metrics import CheckSummary
from frame.domain.models import VideoVersion
from frame.domain.revision import AttributeChange, DefaultPropagationPolicy
from frame.domain.services.comparison import (
    RateDelta,
    RequirementChangeKind,
    SceneStatus,
    VersionNotComparable,
    compare_requirements,
    compare_versions,
    ensure_comparable,
    rate_delta,
)
from tests.support.factories import cafe_ir

R = RequirementChangeKind


def _version(version_id, ir, *, clips=None, parent=None, state=VersionState.COMPLETED):
    return VideoVersion(
        version_id=version_id,
        project_id="prj_1",
        state=state,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        parent_version_id=parent,
        ir=ir,
        clips=clips or {},
    )


def _clip(scene_key, version_id, fingerprint=None):
    return GeneratedClip(
        scene_key=scene_key,
        uri=f"mem://clips/{version_id}/{scene_key}.json",
        duration_ms=4000,
        fingerprint=fingerprint or f"fp-{version_id}-{scene_key}",
    )


def _clips(version_id, ir):
    return {s.scene_key: _clip(s.scene_key, version_id) for s in ir.scenes}


def _teal(ir):
    return DefaultPropagationPolicy().apply(ir, AttributeChange("cup", "color", "teal")).ir


def _scene(comparison, scene_key):
    return next(s for s in comparison.scenes if s.scene_key == scene_key)


# ---- 요구조건 비교 ----------------------------------------------------------------


def test_identical_scene_lists_every_requirement_as_unchanged():
    intro = cafe_ir().scene("intro")
    rows = compare_requirements(intro, intro)
    assert [r.change for r in rows] == [R.UNCHANGED] * len(intro.requirements)
    assert [r.after.key for r in rows] == [r.key for r in intro.requirements]


def test_value_change_keeps_the_key_and_reports_both_values():
    before = cafe_ir().scene("intro")
    after = _teal(cafe_ir()).scene("intro")
    changed = [r for r in compare_requirements(before, after) if r.change is R.CHANGED]
    assert len(changed) == 1
    row = changed[0]
    assert (row.entity_key, row.predicate) == ("cup", "color")
    assert (row.before.expected_value, row.after.expected_value) == ("red", "teal")
    assert row.before.key == row.after.key == "intro:cup:color"


def test_scene_level_condition_change_is_compared_like_any_requirement():
    def scene(location):
        return SceneSpec("a", 0, 4000, requirements=(RequirementSpec("a", "location", location),))

    (row,) = compare_requirements(scene("cafe counter"), scene("park"))
    assert row.change is R.CHANGED
    assert row.entity_key is None and row.predicate == "location"
    assert (row.before.expected_value, row.after.expected_value) == ("cafe counter", "park")


def test_relation_target_change_is_paired_into_one_change():
    def scene(target):
        return SceneSpec(
            "intro",
            0,
            4000,
            entity_keys=("barista", "cup", "phone"),
            requirements=(RequirementSpec("intro", "holds", target, "barista", target),),
        )

    (row,) = compare_requirements(scene("cup"), scene("phone"))
    assert row.change is R.CHANGED
    assert (row.before.key, row.after.key) == (
        "intro:barista:holds:cup",
        "intro:barista:holds:phone",
    )
    assert (row.before.expected_value, row.after.expected_value) == ("cup", "phone")


def test_added_and_removed_requirements_follow_the_after_order_then_removed():
    before = SceneSpec(
        "a",
        0,
        4000,
        requirements=(
            RequirementSpec("a", "lighting", "warm"),
            RequirementSpec("a", "mood", "calm"),
        ),
    )
    after = SceneSpec(
        "a",
        0,
        4000,
        requirements=(
            RequirementSpec("a", "mood", "calm"),
            RequirementSpec("a", "camera", "close-up"),
        ),
    )
    rows = compare_requirements(before, after)
    assert [(r.change, r.predicate) for r in rows] == [
        (R.UNCHANGED, "mood"),
        (R.ADDED, "camera"),
        (R.REMOVED, "lighting"),
    ]
    assert rows[1].before is None and rows[2].after is None


# ---- 장면 비교 -------------------------------------------------------------------


def test_revision_reuses_unchanged_scene_clip_and_regenerates_changed_ones():
    original_ir = cafe_ir()
    original = _version("ver_1", original_ir, clips=_clips("ver_1", original_ir))
    revised_ir = _teal(original_ir)
    clips = _clips("ver_2", revised_ir)
    clips["outro"] = original.clips["outro"]  # 부모 클립을 그대로 재사용
    revision = _version("ver_2", revised_ir, clips=clips, parent="ver_1")

    comparison = compare_versions(original, revision)

    assert [(s.scene_key, s.status, s.conditions_changed) for s in comparison.scenes] == [
        ("intro", SceneStatus.REGENERATED, True),
        ("closeup", SceneStatus.REGENERATED, True),
        ("outro", SceneStatus.REUSED, False),
    ]
    outro = _scene(comparison, "outro")
    assert outro.before_clip == outro.after_clip == original.clips["outro"]
    assert all(r.change is R.UNCHANGED for r in outro.requirements)
    assert comparison.changed_scene_keys == ("intro", "closeup")
    assert comparison.requirement_change_count == 2


def test_same_conditions_with_a_different_clip_is_regenerated_not_reused():
    """재사용은 '같은 산출물(URI)' 이다. 조건이 같아도 따로 생성한 클립은 바이트가 다를 수 있다."""
    ir = cafe_ir()
    shared = "fp-same-conditions"
    left = _version("ver_1", ir, clips={"outro": _clip("outro", "ver_1", shared)})
    right = _version("ver_3", ir, clips={"outro": _clip("outro", "ver_3", shared)})
    outro = _scene(compare_versions(left, right), "outro")
    assert outro.status is SceneStatus.REGENERATED
    assert outro.conditions_changed is False


def test_scene_without_a_clip_on_either_side_is_unavailable_but_conditions_are_compared():
    original_ir = cafe_ir()
    original = _version("ver_1", original_ir, clips=_clips("ver_1", original_ir))
    draft = _version("ver_2", _teal(original_ir), parent="ver_1", state=VersionState.DRAFT)

    comparison = compare_versions(original, draft)

    assert {s.status for s in comparison.scenes} == {SceneStatus.UNAVAILABLE}
    assert comparison.changed_scene_keys == ("intro", "closeup")
    assert _scene(comparison, "intro").after_clip is None
    assert _scene(comparison, "intro").before_clip == original.clips["intro"]


def test_added_and_removed_scenes_come_after_in_after_order_then_removed():
    before_ir = cafe_ir()
    outro = before_ir.scene("outro")
    finale = SceneSpec(
        "finale",
        outro.planned_start_ms,
        outro.planned_end_ms,
        outro.description,
        outro.entity_keys,
        tuple(replace(r, scene_key="finale") for r in outro.requirements),
    )
    after_ir = replace(before_ir, scenes=(*before_ir.scenes[:2], finale))
    before = _version("ver_1", before_ir, clips=_clips("ver_1", before_ir))
    after = _version("ver_2", after_ir, clips=_clips("ver_2", after_ir))

    comparison = compare_versions(before, after)

    assert [(s.scene_key, s.status) for s in comparison.scenes] == [
        ("intro", SceneStatus.REGENERATED),
        ("closeup", SceneStatus.REGENERATED),
        ("finale", SceneStatus.ADDED),
        ("outro", SceneStatus.REMOVED),
    ]
    added, removed = _scene(comparison, "finale"), _scene(comparison, "outro")
    assert added.before_scene is None and added.after_scene == finale
    assert removed.after_scene is None and removed.after_clip is None
    assert {r.change for r in added.requirements} == {R.ADDED}
    assert {r.change for r in removed.requirements} == {R.REMOVED}
    assert added.conditions_changed and removed.conditions_changed
    assert comparison.changed_scene_keys == ("finale", "outro")


def test_entity_definition_change_marks_the_scene_changed_without_requirement_changes():
    before_ir = cafe_ir()
    after_ir = replace(
        before_ir,
        entities=tuple(
            EntitySpec(e.entity_key, e.kind, "검은 앞치마를 두른 바리스타")
            if e.entity_key == "barista"
            else e
            for e in before_ir.entities
        ),
    )
    comparison = compare_versions(_version("ver_1", before_ir), _version("ver_2", after_ir))
    assert comparison.changed_scene_keys == ("intro", "outro")  # 바리스타가 나오는 장면
    assert comparison.requirement_change_count == 0


def test_status_counts_cover_every_status():
    ir = cafe_ir()
    counts = compare_versions(_version("ver_1", ir), _version("ver_2", ir)).status_counts()
    assert counts == {
        SceneStatus.REUSED: 0,
        SceneStatus.REGENERATED: 0,
        SceneStatus.ADDED: 0,
        SceneStatus.REMOVED: 0,
        SceneStatus.UNAVAILABLE: 3,
    }


def test_versions_without_ir_cannot_be_compared():
    structuring = _version("ver_1", None, state=VersionState.STRUCTURING)
    with pytest.raises(VersionNotComparable) as error:
        ensure_comparable(structuring)
    assert error.value.code == "VERSION_NOT_COMPARABLE"
    with pytest.raises(VersionNotComparable):
        compare_versions(_version("ver_2", cafe_ir()), structuring)


# ---- 검수 요약 차이 ---------------------------------------------------------------


def _summary(reflection, consistency, preservation):
    return CheckSummary(
        total=0,
        passed=0,
        failed=0,
        undetermined=0,
        requirement_reflection_rate=reflection,
        consistency_rate=consistency,
        preservation_rate=preservation,
    )


def test_rate_delta_is_after_minus_before_and_null_when_either_side_is_missing():
    delta = rate_delta(_summary(0.5, 1.0, None), _summary(0.75, 0.9, 1.0))
    assert delta.requirement_reflection_rate == pytest.approx(0.25)
    assert delta.consistency_rate == pytest.approx(-0.1)
    assert delta.preservation_rate is None
    empty = RateDelta(None, None, None)
    assert rate_delta(_summary(None, None, None), _summary(1.0, 1.0, 1.0)) == empty
    assert rate_delta(_summary(1.0, 1.0, 1.0), _summary(None, None, None)) == empty
