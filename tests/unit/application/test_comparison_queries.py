"""US-04 조회: 두 버전 비교(AC-43)와 프로젝트 대시보드 집계(AC-41)."""

import pytest

from frame.adapters.checker import (
    FakeConsistencyChecker,
    FakePreservationChecker,
    FakeRequirementChecker,
)
from frame.adapters.generator import FakeVideoGenerator
from frame.application.dto import CreateRevisionCommand
from frame.domain.enums import Verdict, VersionState
from frame.domain.errors import NotFound, ValidationError
from frame.domain.models import VideoVersion
from frame.domain.services.comparison import SceneStatus, VersionNotComparable
from frame.domain.services.propagation import AttributeChange
from tests.support.factories import create_draft, generate, make_container


def _revise(container, version_id, entity="cup", predicate="color", value="teal"):
    return container.create_revision.execute(
        CreateRevisionCommand(version_id, AttributeChange(entity, predicate, value))
    ).version_id


def _statuses(view):
    return {s.scene_key: s.status for s in view.comparison.scenes}


def _sharing(container, **overrides):
    """같은 저장소·ID·시계를 쓰면서 어댑터만 바꾼 컨테이너 (수정 버전만 실패시키는 데 쓴다)."""
    return make_container(
        uow_factory=container.uow_factory,
        ids=container.ids,
        clock=container.clock,
        artifact_store=container.artifact_store,
        **overrides,
    )


def _failing_on_intro(container):
    store = container.artifact_store
    return _sharing(container, generator=FakeVideoGenerator(store, fail_scene_keys={"intro"}))


# ---- 두 버전 비교 ------------------------------------------------------------------


def test_comparison_defaults_to_the_parent_and_reports_reused_clips(container, draft):
    generate(container, draft.version_id)
    revision_id = _revise(container, draft.version_id)
    generate(container, revision_id)

    view = container.queries.compare_versions(revision_id)

    assert view.before.version.version_id == draft.version_id
    assert view.after.version.version_id == revision_id
    assert view.against_is_parent is True
    assert _statuses(view) == {
        "intro": SceneStatus.REGENERATED,
        "closeup": SceneStatus.REGENERATED,
        "outro": SceneStatus.REUSED,
    }
    assert view.comparison.changed_scene_keys == ("intro", "closeup")
    assert view.before.summary.preservation_rate is None  # 원본에는 보존 검수가 없다
    assert view.after.summary.preservation_rate >= 0.9
    assert view.checks_delta.requirement_reflection_rate == 0.0
    assert view.checks_delta.preservation_rate is None


def test_comparison_against_a_sibling_version(container, draft):
    generate(container, draft.version_id)
    teal = _revise(container, draft.version_id)
    black = _revise(container, draft.version_id, "barista", "shirt_color", "black")
    generate(container, teal)
    generate(container, black)

    view = container.queries.compare_versions(black, against=teal)

    assert view.against_is_parent is False
    assert view.before.version.version_id == teal
    assert view.comparison.changed_scene_keys == ("intro", "closeup", "outro")
    assert set(_statuses(view).values()) == {SceneStatus.REGENERATED}
    intro = next(s for s in view.comparison.scenes if s.scene_key == "intro")
    assert {
        (r.predicate, r.before.expected_value, r.after.expected_value)
        for r in intro.requirement_changes
    } == {("color", "teal", "red"), ("shirt_color", "white", "black")}


def test_each_side_carries_requirement_verdicts_by_key():
    container = make_container(
        checkers=(
            FakeRequirementChecker(failing_keys={"intro:cup:color"}),
            FakeConsistencyChecker(),
            FakePreservationChecker(),
        )
    )
    draft = create_draft(container)
    generate(container, draft.version_id)
    revision_id = _revise(container, draft.version_id)
    generate(container, revision_id)

    view = container.queries.compare_versions(revision_id)

    assert view.before.requirement_verdicts["intro:cup:color"] is Verdict.FAIL
    assert view.after.requirement_verdicts["intro:cup:color"] is Verdict.FAIL
    assert view.after.requirement_verdicts["closeup:cup:color"] is Verdict.PASS
    assert view.after.summary.requirement_reflection_rate == pytest.approx(7 / 8)


def test_draft_revision_can_be_compared_before_generation(container, draft):
    generate(container, draft.version_id)
    revision_id = _revise(container, draft.version_id)

    view = container.queries.compare_versions(revision_id)

    assert view.after.version.state is VersionState.DRAFT
    assert set(_statuses(view).values()) == {SceneStatus.UNAVAILABLE}
    assert view.comparison.changed_scene_keys == ("intro", "closeup")  # 재생성될 장면
    assert view.after.summary.total == 0
    assert view.after.requirement_verdicts == {}
    assert view.checks_delta.requirement_reflection_rate is None


def test_failed_revision_can_be_compared_with_its_error(container, draft):
    generate(container, draft.version_id)
    failing = _failing_on_intro(container)
    revision_id = _revise(failing, draft.version_id)
    generate(failing, revision_id)

    view = container.queries.compare_versions(revision_id)

    assert view.after.version.state is VersionState.FAILED
    assert view.after.version.error_code == "GENERATION_ERROR"
    assert set(_statuses(view).values()) == {SceneStatus.UNAVAILABLE}
    assert view.comparison.changed_scene_keys == ("intro", "closeup")


def test_comparison_rejects_unknown_versions_other_projects_and_missing_parent(container, draft):
    with pytest.raises(NotFound):
        container.queries.compare_versions("ver_missing")
    with pytest.raises(ValidationError, match="부모"):
        container.queries.compare_versions(draft.version_id)  # 원본은 부모가 없다
    with pytest.raises(NotFound):
        container.queries.compare_versions(draft.version_id, against="ver_missing")
    other = create_draft(container, title="다른 프로젝트")
    with pytest.raises(ValidationError, match="같은 프로젝트"):
        container.queries.compare_versions(draft.version_id, against=other.version_id)


def test_versions_without_ir_are_not_comparable(container, draft):
    failed = create_draft(container, prompt="자연어만 있는 요구사항")  # 구조화 실패 -> IR 없음
    with pytest.raises(VersionNotComparable):
        container.queries.compare_versions(failed.version_id)
    with container.uow_factory() as uow:
        uow.versions.add(
            VideoVersion(
                version_id="ver_structuring",
                project_id=draft.project_id,
                state=VersionState.STRUCTURING,
                created_at=container.clock.now(),
            )
        )
        uow.commit()
    with pytest.raises(VersionNotComparable) as error:
        container.queries.compare_versions(draft.version_id, against="ver_structuring")
    assert error.value.code == "VERSION_NOT_COMPARABLE"


# ---- 프로젝트 대시보드 -------------------------------------------------------------


def test_dashboard_builds_the_version_tree_with_states_and_summaries(container, draft):
    root = draft.version_id
    generate(container, root)
    teal = _revise(container, root)  # 1단계: 컵이 나오는 2장면
    generate(container, teal)
    black = _revise(container, root, "barista", "shirt_color", "black")  # 형제, DRAFT 로 둔다
    failing = _failing_on_intro(container)
    cool = _revise(failing, teal, "cafe", "lighting", "cool")  # 2단계: 3장면, 생성 실패
    generate(failing, cool)

    view = container.queries.get_dashboard(draft.project_id)

    assert view.project.project_id == draft.project_id
    assert [n.overview.version.version_id for n in view.versions] == [root, teal, black, cool]
    nodes = {n.overview.version.version_id: n for n in view.versions}
    assert [nodes[v].depth for v in (root, teal, black, cool)] == [0, 1, 1, 2]
    assert nodes[root].child_version_ids == (teal, black)
    assert nodes[teal].child_version_ids == (cool,)
    assert nodes[cool].child_version_ids == ()
    assert [nodes[v].changed_scene_count for v in (root, teal, black, cool)] == [None, 2, 2, 3]
    assert {n.scene_count for n in view.versions} == {3}
    assert nodes[teal].overview.summary.preservation_rate >= 0.9
    assert nodes[cool].overview.version.error_code == "GENERATION_ERROR"
    assert view.state_counts == {
        VersionState.STRUCTURING: 0,
        VersionState.DRAFT: 1,
        VersionState.QUEUED: 0,
        VersionState.PROCESSING: 0,
        VersionState.COMPLETED: 2,
        VersionState.FAILED: 1,
    }
    assert view.latest_version_id == cool
    assert view.latest_completed_version_id == teal


def test_dashboard_lists_versions_without_ir(container):
    failed = create_draft(container, prompt="자연어만 있는 요구사항")

    view = container.queries.get_dashboard(failed.project_id)

    (node,) = view.versions
    assert node.overview.version.state is VersionState.FAILED
    assert (node.depth, node.scene_count, node.changed_scene_count) == (0, None, None)
    assert view.state_counts[VersionState.FAILED] == 1
    assert view.latest_version_id == failed.version_id
    assert view.latest_completed_version_id is None


def test_dashboard_of_unknown_project_is_not_found(container):
    with pytest.raises(NotFound):
        container.queries.get_dashboard("prj_missing")
