"""US-06: 요구사항 접수(STRUCTURING) -> 비동기 구조화 -> DRAFT | FAILED(오류 원인 저장)."""

import pytest

from frame.adapters.structurer import FakeStructurer
from frame.application.dto import CreateProjectCommand
from frame.application.errors import StructuringError
from frame.application.jobs import STRUCTURE_REQUIREMENTS
from frame.application.ports.structurer import ReferenceImageInput
from frame.domain.enums import VersionState
from frame.domain.errors import ValidationError
from tests.support.factories import (
    CAFE_PROMPT,
    create_draft,
    dispatcher_of,
    make_container,
    version_of,
)


def test_create_project_persists_structuring_version_and_dispatches_job(container, dispatcher):
    result = container.create_project.execute(CreateProjectCommand("카페", CAFE_PROMPT))

    assert result.state is VersionState.STRUCTURING
    assert version_of(container, result.version_id).state is VersionState.STRUCTURING
    assert [j.name for j in dispatcher.pending] == [STRUCTURE_REQUIREMENTS]
    assert dispatcher.pending[0].payload == {"version_id": result.version_id}


@pytest.mark.ac("AC-11")
def test_structuring_job_turns_version_into_draft_with_ir(container):
    result = create_draft(container)

    version = version_of(container, result.version_id)
    assert version.state is VersionState.DRAFT
    assert version.ir is not None
    assert [s.scene_key for s in version.ir.scenes] == ["intro", "closeup", "outro"]
    assert {e.entity_key for e in version.ir.entities} == {"barista", "cup", "cafe"}
    assert len(list(version.ir.iter_requirements())) == 8
    assert version.source_prompt == CAFE_PROMPT  # 원문은 보존된다


def test_project_lists_its_versions(container):
    result = create_draft(container)
    detail = container.queries.get_project(result.project_id)
    assert detail.project.title == "카페 신제품 숏폼"
    assert [v.version_id for v in detail.versions] == [result.version_id]


@pytest.mark.parametrize("prompt", ["", "   \n"])
def test_empty_prompt_is_rejected_synchronously(container, prompt):
    with pytest.raises(ValidationError):
        container.create_project.execute(CreateProjectCommand("카페", prompt))


def test_structurer_failure_is_recorded_as_failed_with_error_info():
    container = make_container(structurer=FakeStructurer(error=StructuringError("모델 응답 없음")))
    result = create_draft(container)

    version = version_of(container, result.version_id)
    assert version.state is VersionState.FAILED
    assert version.error_code == "STRUCTURING_ERROR"
    assert "모델 응답 없음" in (version.error_message or "")
    assert version.ir is None


def test_unparseable_prompt_fails_with_reason(container):
    result = create_draft(container, prompt="그냥 멋진 카페 영상 만들어줘")
    version = version_of(container, result.version_id)
    assert version.state is VersionState.FAILED
    assert version.error_code == "STRUCTURING_ERROR"
    assert "줄 1" in (version.error_message or "")


def test_reference_image_for_unknown_entity_fails_validation(container):
    result = create_draft(
        container,
        references=[ReferenceImageInput("https://example.com/refs/logo.png", entity_key="logo")],
    )
    version = version_of(container, result.version_id)
    assert version.state is VersionState.FAILED
    assert version.error_code == "VALIDATION_ERROR"


def test_reference_images_are_stored_with_the_version(container):
    refs = [
        ReferenceImageInput("https://example.com/refs/cup.png", "cup"),
        ReferenceImageInput("https://example.com/refs/style.png"),
    ]
    result = create_draft(container, references=refs)
    stored = container.queries.get_version(result.version_id).reference_images
    assert [(r.image_uri, r.entity_key) for r in stored] == [
        (r.image_uri, r.entity_key) for r in refs
    ]


def test_structuring_job_is_idempotent(container, dispatcher):
    result = create_draft(container)
    before = version_of(container, result.version_id)
    container.structure_requirements.execute(result.version_id)  # 재시도
    assert version_of(container, result.version_id) == before


def test_target_duration_mismatch_fails_validation(container):
    result = container.create_project.execute(
        CreateProjectCommand("카페", CAFE_PROMPT, target_duration_ms=30000)  # 장면 합계는 12000
    )
    dispatcher_of(container).run_all()
    version = version_of(container, result.version_id)
    assert (version.state, version.error_code) == (VersionState.FAILED, "VALIDATION_ERROR")
    assert "목표 길이" in version.error_message


def test_target_duration_within_tolerance_is_accepted(container):
    result = container.create_project.execute(
        CreateProjectCommand("카페", CAFE_PROMPT, target_duration_ms=12400)
    )
    dispatcher_of(container).run_all()
    assert version_of(container, result.version_id).state is VersionState.DRAFT


def test_dsl_keys_that_could_escape_their_folder_fail_structuring(container):
    """감사 A-01: DSL 은 공백 아닌 글자를 그대로 키로 쓰므로 도메인 검증이 막는다 (키는 클립 저장 경로에 들어간다)."""
    prompt = (
        "scene ../ver_victim/intro 0-1000\nentity cup PROP\n"
        "in ../ver_victim/intro: cup\ncup.color = red"
    )

    result = create_draft(container, prompt=prompt)

    version = version_of(container, result.version_id)
    assert (version.state, version.error_code) == (VersionState.FAILED, "STRUCTURING_ERROR")
    assert "장면 키" in version.error_message
