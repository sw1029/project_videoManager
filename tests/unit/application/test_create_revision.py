"""US-01: 수정 버전 생성은 원본을 보존하고, parent 링크가 있는 새 DRAFT 를 만든다."""

import pytest

from frame.application.dto import CreateProjectCommand, CreateRevisionCommand
from frame.application.ports.structurer import ReferenceImageInput
from frame.domain.enums import ChangeScope, KeepAspect, RevisionKind, VersionState
from frame.domain.errors import NotFound, ValidationError, VersionNotRevisable
from frame.domain.revision import KeepItem
from frame.domain.services.propagation import AttributeChange
from tests.support.factories import CAFE_PROMPT, create_draft, generate, version_of


def _revise(container, version_id, **kwargs):
    change = AttributeChange(
        **{"entity_key": "cup", "predicate": "color", "new_value": "teal", **kwargs}
    )
    return container.create_revision.execute(CreateRevisionCommand(version_id, change))


@pytest.mark.ac("AC-13")
def test_revision_creates_new_draft_linked_to_parent(container, draft):
    result = _revise(container, draft.version_id)

    revision = version_of(container, result.version_id)
    assert result.state is VersionState.DRAFT
    assert result.parent_version_id == draft.version_id
    assert result.changed_scene_keys == ("intro", "closeup")
    assert revision.parent_version_id == draft.version_id
    assert revision.source_prompt == CAFE_PROMPT  # 프롬프트를 다시 쓰지 않고 조건만 바뀐다
    assert {
        r.expected_value for r in revision.ir.iter_requirements() if r.predicate == "color"
    } == {"teal"}


def test_parent_version_is_untouched(container, draft):
    before = version_of(container, draft.version_id)
    _revise(container, draft.version_id)
    assert version_of(container, draft.version_id) == before


def test_reference_images_are_copied_to_revision(container):
    refs = [ReferenceImageInput("mem://refs/cup.png", "cup")]
    draft = create_draft(container, references=refs)
    result = _revise(container, draft.version_id)
    copied = container.queries.get_version(result.version_id).reference_images
    assert [(r.image_uri, r.entity_key) for r in copied] == [("mem://refs/cup.png", "cup")]
    assert copied[0].version_id == result.version_id


def test_revision_of_completed_version_is_allowed_and_keeps_seed(container, draft):
    completed = generate(container, draft.version_id)
    assert completed.state is VersionState.COMPLETED
    result = _revise(container, draft.version_id)
    assert version_of(container, result.version_id).seed == completed.seed


def test_revision_is_rejected_while_structuring(container, dispatcher):
    created = container.create_project.execute(CreateProjectCommand("카페", CAFE_PROMPT))
    with pytest.raises(VersionNotRevisable) as excinfo:
        _revise(container, created.version_id)
    assert excinfo.value.code == "VERSION_NOT_REVISABLE"


def test_revision_is_rejected_while_queued_or_processing(container, draft):
    container.accept_generation.execute(draft.version_id)  # QUEUED (작업은 아직 실행 안 함)
    with pytest.raises(VersionNotRevisable):
        _revise(container, draft.version_id)


def test_unknown_entity_is_not_found(container, draft):
    with pytest.raises(NotFound):
        _revise(container, draft.version_id, entity_key="ghost")


def test_noop_change_is_rejected(container, draft):
    with pytest.raises(ValidationError):
        _revise(container, draft.version_id, new_value="red")


def test_unknown_version_is_not_found(container):
    with pytest.raises(NotFound):
        _revise(container, "ver_missing")


def test_scope_scenes_changes_only_requested_scene(container, draft):
    result = _revise(container, draft.version_id, scope=ChangeScope.SCENES, scene_keys=("closeup",))
    assert result.changed_scene_keys == ("closeup",)


# ---- 수정 의도: 유지할 요소와 REPAIR (ADR-0017) ------------------------------------------


def _command(version_id, *, kind=RevisionKind.CHANGE, keep=(), request_text=None, **kwargs):
    change = AttributeChange(
        **{"entity_key": "cup", "predicate": "color", "new_value": "teal", **kwargs}
    )
    return CreateRevisionCommand(
        version_id, change, kind=kind, keep=tuple(keep), request_text=request_text
    )


def test_change_revision_records_what_was_asked(container, draft):
    keep = (KeepItem(KeepAspect.ACTION, "barista"), KeepItem(KeepAspect.LIGHTING))
    text = "컵 색상만 청록색으로 동일해줘. 인물과 배경은 유지해줘."

    result = container.create_revision.execute(
        _command(draft.version_id, keep=keep, request_text=f"  {text}  ")
    )

    intent = version_of(container, result.version_id).intent
    assert result.kind is RevisionKind.CHANGE and intent.kind is RevisionKind.CHANGE
    assert (intent.entity_key, intent.predicate, intent.value) == ("cup", "color", "teal")
    assert intent.previous_value == "red"
    assert intent.target_scene_keys == result.changed_scene_keys == ("intro", "closeup")
    assert intent.keep == keep and intent.request_text == text
    assert version_of(container, draft.version_id).intent is None  # 원본에는 의도가 없다


def test_noop_change_points_to_repair(container, draft):
    with pytest.raises(ValidationError, match="REPAIR"):
        _revise(container, draft.version_id, new_value="red")


@pytest.mark.ac("AC-01")
def test_repair_keeps_the_requirements_and_reseeds_the_target_scenes(container, draft):
    """컨셉 화면의 흐름: 요구는 그대로인데 영상이 어긋났다 -> 그 장면만 다시 만든다."""
    parent = version_of(container, draft.version_id)

    result = container.create_revision.execute(
        _command(draft.version_id, kind=RevisionKind.REPAIR, new_value="red")
    )

    revision = version_of(container, result.version_id)
    assert result.kind is RevisionKind.REPAIR
    assert result.changed_scene_keys == ("intro", "closeup")  # 다시 만들 장면
    assert revision.ir.fingerprint() == parent.ir.fingerprint()  # 요구조건은 그대로다
    assert set(revision.scene_seeds) == {"intro", "closeup"}
    for scene_key in ("intro", "closeup"):
        assert revision.seed_for(scene_key) != parent.seed_for(scene_key)
    assert revision.seed_for("outro") == parent.seed_for("outro")
    assert revision.intent.kind is RevisionKind.REPAIR and revision.intent.value == "red"
    assert revision.intent.previous_value is None


def test_repair_is_not_a_way_to_change_a_value(container, draft):
    with pytest.raises(ValidationError, match="CHANGE"):
        container.create_revision.execute(_command(draft.version_id, kind=RevisionKind.REPAIR))


def test_repairing_again_uses_yet_another_seed(container, draft):
    first = container.create_revision.execute(
        _command(
            draft.version_id,
            kind=RevisionKind.REPAIR,
            new_value="red",
            scope=ChangeScope.SCENES,
            scene_keys=("closeup",),
        )
    )
    second = container.create_revision.execute(
        _command(first.version_id, kind=RevisionKind.REPAIR, new_value="red")
    )

    one, two = version_of(container, first.version_id), version_of(container, second.version_id)
    assert set(one.scene_seeds) == {"closeup"}
    assert two.seed_for("closeup") != one.seed_for("closeup")
    assert two.seed_for("closeup") != version_of(container, draft.version_id).seed_for("closeup")


def test_a_later_change_inherits_the_reseeded_scenes(container, draft):
    """REPAIR 로 다시 만든 장면의 seed 를 물려받아야 그 장면이 다음 수정 버전에서 재사용된다."""
    repaired = container.create_revision.execute(
        _command(draft.version_id, kind=RevisionKind.REPAIR, new_value="red")
    )
    changed = container.create_revision.execute(
        _command(
            repaired.version_id, entity_key="barista", predicate="shirt_color", new_value="blue"
        )
    )

    assert (
        version_of(container, changed.version_id).scene_seeds
        == version_of(container, repaired.version_id).scene_seeds
    )


def test_keep_items_are_validated(container, draft):
    with pytest.raises(NotFound):
        container.create_revision.execute(
            _command(draft.version_id, keep=[KeepItem(KeepAspect.ACTION, "ghost")])
        )
    with pytest.raises(ValidationError, match="유지"):
        container.create_revision.execute(
            _command(draft.version_id, keep=[KeepItem(KeepAspect.APPEARANCE, "cup")])
        )
