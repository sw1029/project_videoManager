"""#11 DRAFT IR 수정 (리스크 2 사용자 확인·수정): DRAFT 에서만 보정 -> 검증 -> 저장. 생성 접수 뒤에는 409."""

from collections.abc import Mapping
from typing import Any

import pytest

from frame.adapters.generator.fake import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore
from frame.adapters.structurer import FakeStructurer, PassthroughStructurer
from frame.application.dto import CreateProjectCommand, CreateRevisionCommand
from frame.application.errors import StructuringError
from frame.application.ports.structurer import (
    IrDocumentStructurer,
    ReferenceImageInput,
    StructuredDocument,
)
from frame.application.use_cases.edit_draft_ir import EditDraftIrCommand
from frame.domain.enums import ReferenceOrigin, VersionState
from frame.domain.errors import NotFound, ValidationError, VersionNotEditable
from frame.domain.models import ReferenceImage
from frame.domain.revision import AttributeChange
from tests.support.factories import (
    CAFE_PROMPT,
    create_draft,
    dispatcher_of,
    generate,
    make_container,
    version_of,
)


def _teal_cup_document(container, version_id: str) -> dict[str, Any]:
    """intro 의 컵 색을 청록으로 바꾸고 closeup 구간을 어긋나게 둔 IR 문서 (블록 화면에서 고친 결과)."""
    document = version_of(container, version_id).ir.to_dict()
    intro = document["scenes"][0]
    for requirement in intro["requirements"]:
        if requirement["entity_key"] == "cup" and requirement["predicate"] == "color":
            requirement["expected_value"] = "teal"
    document["scenes"][1]["planned_start_ms"] = 4200  # 보정이 4000 으로 붙인다
    document["scenes"][1]["planned_end_ms"] = 8200
    return document


def _cup_color(version, scene_key: str) -> str:
    scene = version.ir.scene(scene_key)
    return next(r.expected_value for r in scene.requirements if r.key == f"{scene_key}:cup:color")


@pytest.mark.ac("AC-13")
def test_draft_ir_is_repaired_validated_and_saved(container, draft):
    result = container.edit_draft_ir.execute(
        EditDraftIrCommand(draft.version_id, _teal_cup_document(container, draft.version_id))
    )

    version = version_of(container, draft.version_id)
    assert (result.version_id, result.state) == (draft.version_id, VersionState.DRAFT)
    assert version.state is VersionState.DRAFT and version.frozen_fingerprint is None
    assert _cup_color(version, "intro") == "teal"
    assert _cup_color(version, "closeup") == "red"  # 고친 장면만 바뀐다
    assert version.ir.scene("closeup").planned_start_ms == 4000
    assert len(result.repair_notes) == 1 and "closeup" in result.repair_notes[0]
    assert version.source_prompt == CAFE_PROMPT  # 원문은 그대로 남는다


def test_generation_freezes_and_uses_the_edited_ir():
    """완료 기준: 구조화 결과를 고친 뒤 생성을 접수할 수 있다 (고친 IR 로 조건이 고정되고 생성된다)."""
    store = InMemoryArtifactStore()
    generator = FakeVideoGenerator(store)
    container = make_container(artifact_store=store, generator=generator)
    draft = create_draft(container)
    container.edit_draft_ir.execute(
        EditDraftIrCommand(draft.version_id, _teal_cup_document(container, draft.version_id))
    )
    edited = version_of(container, draft.version_id).ir

    done = generate(container, draft.version_id)

    assert done.state is VersionState.COMPLETED
    assert done.frozen_fingerprint == edited.fingerprint()
    requested = {r.scene.scene_key: r.scene for r in generator.requests}
    assert requested["intro"] == edited.scene("intro")


def test_invalid_document_is_rejected_and_the_ir_is_kept(container, draft):
    before = version_of(container, draft.version_id).ir
    document = before.to_dict()
    document["scenes"][1]["scene_key"] = "intro"  # 장면 키 중복은 고칠 수 없다

    with pytest.raises(ValidationError) as excinfo:
        container.edit_draft_ir.execute(EditDraftIrCommand(draft.version_id, document))

    assert "scene_key" in excinfo.value.message
    assert version_of(container, draft.version_id).ir == before


@pytest.mark.parametrize("reach", ["queued", "completed"])
def test_versions_past_draft_keep_their_frozen_ir(container, draft, reach):
    container.accept_generation.execute(draft.version_id)
    if reach == "completed":
        dispatcher_of(container).run_all()
    before = version_of(container, draft.version_id)
    document = _teal_cup_document(container, draft.version_id)

    with pytest.raises(VersionNotEditable) as excinfo:
        container.edit_draft_ir.execute(EditDraftIrCommand(draft.version_id, document))

    assert excinfo.value.code == "VERSION_NOT_EDITABLE"
    after = version_of(container, draft.version_id)
    assert (after.ir, after.frozen_fingerprint) == (before.ir, before.frozen_fingerprint)


def test_versions_without_ir_are_not_editable(container):
    structuring = container.create_project.execute(CreateProjectCommand("카페", CAFE_PROMPT))
    with pytest.raises(VersionNotEditable):  # 아직 구조화 중
        container.edit_draft_ir.execute(EditDraftIrCommand(structuring.version_id, {}))

    failing = make_container(structurer=FakeStructurer(error=StructuringError("응답 없음")))
    failed = create_draft(failing)
    with pytest.raises(VersionNotEditable):  # 구조화 실패 (FAILED)
        failing.edit_draft_ir.execute(EditDraftIrCommand(failed.version_id, {}))


def test_unknown_version_is_not_found(container):
    with pytest.raises(NotFound):
        container.edit_draft_ir.execute(EditDraftIrCommand("ver_missing", {}))


def test_objects_with_reference_images_must_stay(container):
    draft = create_draft(
        container, references=[ReferenceImageInput("https://example.com/refs/cup.png", "cup")]
    )
    document = version_of(container, draft.version_id).ir.to_dict()
    document["entities"] = [e for e in document["entities"] if e["entity_key"] != "cup"]
    for scene in document["scenes"]:
        scene["entity_keys"] = [k for k in scene["entity_keys"] if k != "cup"]
        scene["requirements"] = [
            r
            for r in scene["requirements"]
            if "cup" not in (r["entity_key"], r["related_entity_key"])
        ]

    with pytest.raises(ValidationError) as excinfo:
        container.edit_draft_ir.execute(EditDraftIrCommand(draft.version_id, document))

    assert "참조 이미지" in excinfo.value.message
    assert version_of(container, draft.version_id).ir.has_entity("cup")


class _AcceptsGenerationMeanwhile(IrDocumentStructurer):
    """보정하는 사이에 다른 요청이 생성을 접수한 상황을 만든다 (DRAFT 를 읽은 뒤, 저장하기 전)."""

    def __init__(self) -> None:
        self.container = None
        self.version_id = ""
        self._inner = PassthroughStructurer()

    def structure_document(self, document: Mapping[str, Any]) -> StructuredDocument:
        self.container.accept_generation.execute(self.version_id)
        return self._inner.structure_document(document)


def test_generation_accepted_while_editing_wins_and_the_frozen_ir_is_kept():
    racing = _AcceptsGenerationMeanwhile()
    container = make_container(ir_structurer=racing)
    racing.container = container
    draft = create_draft(container)
    racing.version_id = draft.version_id
    before = version_of(container, draft.version_id).ir

    with pytest.raises(VersionNotEditable):
        container.edit_draft_ir.execute(
            EditDraftIrCommand(draft.version_id, _teal_cup_document(container, draft.version_id))
        )

    version = version_of(container, draft.version_id)
    assert version.state is VersionState.QUEUED
    assert version.ir == before and version.frozen_fingerprint == before.fingerprint()


def test_renaming_objects_in_a_revision_draft_does_not_regenerate_scenes():
    """표시 이름(#10)은 생성 조건이 아니다: 수정 버전 DRAFT 에서 이름만 붙여도 바뀌지 않은 장면은 부모 클립을 쓴다."""
    store = InMemoryArtifactStore()
    generator = FakeVideoGenerator(store)
    container = make_container(artifact_store=store, generator=generator)
    draft = create_draft(container)
    generate(container, draft.version_id)
    revision = container.create_revision.execute(
        CreateRevisionCommand(
            draft.version_id, AttributeChange(entity_key="cup", predicate="color", new_value="teal")
        )
    )
    document = version_of(container, revision.version_id).ir.to_dict()
    for entity in document["entities"]:
        entity["display_name"] = {"cup": "컵 01", "barista": "바리스타"}.get(entity["entity_key"])

    result = container.edit_draft_ir.execute(EditDraftIrCommand(revision.version_id, document))
    generator.requests.clear()
    done = generate(container, revision.version_id)

    assert result.repair_notes == ()
    assert done.ir.entity("cup").display_name == "컵 01"
    assert sorted(r.scene.scene_key for r in generator.requests) == ["closeup", "intro"]


def test_identity_references_do_not_block_removing_an_object(container, draft):
    """정체성 기준 이미지(DERIVED, 시스템이 뽑은 것)는 사용자 참조가 아니므로 객체를 지우는 것을 막지 않는다."""
    with container.uow_factory() as uow:
        uow.reference_images.add(
            ReferenceImage(
                "img_identity",
                draft.version_id,
                "mem://identity/barista.png",
                "barista",
                origin=ReferenceOrigin.DERIVED,
                source_scene_key="intro",
            )
        )
        uow.commit()
    document = version_of(container, draft.version_id).ir.to_dict()
    document["entities"] = [e for e in document["entities"] if e["entity_key"] != "barista"]
    for scene in document["scenes"]:
        scene["entity_keys"] = [k for k in scene["entity_keys"] if k != "barista"]
        scene["requirements"] = [
            r
            for r in scene["requirements"]
            if "barista" not in (r["entity_key"], r["related_entity_key"])
        ]

    container.edit_draft_ir.execute(EditDraftIrCommand(draft.version_id, document))

    assert not version_of(container, draft.version_id).ir.has_entity("barista")
