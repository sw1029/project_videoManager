"""#10 IR 직접 입력: 블록으로 조립한 IR 문서는 LLM 없이 보정·검증만 거쳐 바로 DRAFT 가 된다 (아이디어 3, T4)."""

import pytest

from frame.adapters.structurer import FakeStructurer
from frame.application.dto import CreateProjectCommand
from frame.application.ports.structurer import ReferenceImageInput
from frame.domain.enums import VersionState
from frame.domain.errors import NotFound, ValidationError
from tests.support.factories import (
    cafe_block_document,
    dispatcher_of,
    generate,
    make_container,
    named_cafe_ir,
    version_of,
)


def _create(container, document=None, prompt="", **kwargs):
    return container.create_project.execute(
        CreateProjectCommand(
            "카페 블록",
            prompt,
            ir=cafe_block_document() if document is None else document,
            **kwargs,
        )
    )


def test_blocks_become_a_draft_without_structuring():
    structurer = FakeStructurer()
    container = make_container(structurer=structurer)

    result = _create(container)

    version = version_of(container, result.version_id)
    assert result.state is VersionState.DRAFT and version.state is VersionState.DRAFT
    assert version.ir == named_cafe_ir()
    assert result.repair_notes == ()
    assert version.source_prompt is None
    assert dispatcher_of(container).pending == []  # 구조화 작업을 넘기지 않는다
    assert structurer.calls == []  # 모델(구조화기)을 부르지 않는다


def test_prompt_given_with_ir_is_kept_only_as_the_source_prompt():
    structurer = FakeStructurer()
    container = make_container(structurer=structurer)

    result = _create(container, prompt="바리스타가 빨간 컵을 들어 보인다")

    version = version_of(container, result.version_id)
    assert version.ir == named_cafe_ir()  # ir 이 우선한다
    assert version.source_prompt == "바리스타가 빨간 컵을 들어 보인다"
    assert structurer.calls == [] and dispatcher_of(container).pending == []


def test_repairs_are_applied_and_reported(container):
    document = cafe_block_document()
    document["scenes"][2]["planned_start_ms"] = 9000  # closeup 과 사이가 빈다
    document["scenes"][2]["planned_end_ms"] = 13000
    document["entities"][1]["kind"] = "product"  # 소품의 별칭

    result = _create(container, document)

    version = version_of(container, result.version_id)
    assert version.ir == named_cafe_ir()
    assert len(result.repair_notes) == 1 and "outro" in result.repair_notes[0]


def test_invalid_ir_is_rejected_at_once_and_nothing_is_saved(container):
    document = cafe_block_document()
    document["scenes"][0]["requirements"][0]["expected_value"] = ""  # 컵 색이 비었다

    with pytest.raises(ValidationError) as excinfo:
        _create(container, document)

    assert "값이 비어" in excinfo.value.message
    with pytest.raises(NotFound):
        container.queries.get_project("prj_0001")
    assert dispatcher_of(container).pending == []


def test_reference_images_must_point_to_objects_of_the_ir(container):
    with pytest.raises(ValidationError) as excinfo:
        _create(container, reference_images=(ReferenceImageInput("mem://refs/logo.png", "logo"),))
    assert "참조 이미지" in excinfo.value.message

    result = _create(
        container, reference_images=(ReferenceImageInput("mem://refs/cup.png", "cup"),)
    )
    stored = container.queries.get_version(result.version_id).reference_images
    assert [(r.image_uri, r.entity_key) for r in stored] == [("mem://refs/cup.png", "cup")]


def test_target_duration_is_checked_against_the_scenes(container):
    with pytest.raises(ValidationError) as excinfo:
        _create(container, target_duration_ms=30_000)  # 장면 합계는 12000 ms
    assert "목표 길이" in excinfo.value.message

    assert _create(container, target_duration_ms=12_400).state is VersionState.DRAFT


def test_title_is_still_required(container):
    with pytest.raises(ValidationError):
        container.create_project.execute(CreateProjectCommand("  ", "", ir=cafe_block_document()))


def test_a_block_draft_can_be_generated(container):
    result = _create(container, seed=7)

    done = generate(container, result.version_id)

    assert done.state is VersionState.COMPLETED
    assert done.seed == 7
    assert set(done.clips) == {"intro", "closeup", "outro"}
