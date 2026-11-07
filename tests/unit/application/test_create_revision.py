"""US-01: 수정 버전 생성은 원본을 보존하고, parent 링크가 있는 새 DRAFT 를 만든다."""

import pytest

from frame.application.dto import CreateProjectCommand, CreateRevisionCommand
from frame.application.ports.structurer import ReferenceImageInput
from frame.domain.enums import ChangeScope, VersionState
from frame.domain.errors import NotFound, ValidationError, VersionNotRevisable
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
