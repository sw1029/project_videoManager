"""#12 참조 이미지 업로드: 형식·크기 검사 -> refs/uploads/ 에 저장 -> 프로젝트를 만들 때 refs/{project_id}/ 로."""

import pytest

from frame.adapters.generator.fake import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
from frame.application.dto import CreateProjectCommand, CreateRevisionCommand
from frame.application.ports.structurer import ReferenceImageInput
from frame.application.use_cases.upload_reference_image import (
    UploadReferenceImage,
    sniff_image_type,
)
from frame.domain.errors import NotFound, PayloadTooLarge, UnsupportedMediaType, ValidationError
from frame.domain.services.propagation import AttributeChange
from tests.support.factories import (
    CAFE_PROMPT,
    cafe_block_document,
    create_draft,
    generate,
    make_container,
    version_of,
)
from tests.support.images import GIF_BYTES, jpeg_bytes, png_bytes, webp_bytes


@pytest.mark.parametrize(
    ("data", "media_type"),
    [(png_bytes(), "image/png"), (jpeg_bytes(), "image/jpeg"), (webp_bytes(), "image/webp")],
)
def test_png_jpeg_and_webp_are_recognised_by_their_content(data, media_type):
    assert sniff_image_type(data) == media_type


@pytest.mark.parametrize("data", [GIF_BYTES, b"%PDF-1.7", b"<svg/>", b"\x89PN", b""])
def test_other_content_is_not_an_accepted_image(data):
    assert sniff_image_type(data) is None


def test_upload_is_stored_in_the_staging_area_and_returns_its_uri(container):
    upload = container.upload_reference_image.execute(png_bytes())

    assert upload.image_uri == "mem://refs/uploads/upl_0001.png"
    assert (upload.media_type, upload.size_bytes) == ("image/png", len(png_bytes()))
    store = container.artifact_store
    assert store.get(upload.image_uri) == png_bytes()
    assert store.content_type(upload.image_uri) == "image/png"


def test_the_extension_follows_the_content_not_the_client(container):
    assert container.upload_reference_image.execute(jpeg_bytes()).image_uri.endswith(".jpg")
    assert container.upload_reference_image.execute(webp_bytes()).image_uri.endswith(".webp")


def test_unsupported_formats_are_rejected(container):
    with pytest.raises(UnsupportedMediaType) as excinfo:
        container.upload_reference_image.execute(GIF_BYTES)
    assert excinfo.value.code == "UNSUPPORTED_MEDIA_TYPE"
    assert "PNG" in excinfo.value.message


def test_empty_files_are_rejected(container):
    with pytest.raises(ValidationError):
        container.upload_reference_image.execute(b"")


def test_files_over_the_size_limit_are_rejected():
    store = InMemoryArtifactStore()
    upload = UploadReferenceImage(store, make_container().ids, max_bytes=len(png_bytes()) - 1)

    with pytest.raises(PayloadTooLarge) as excinfo:
        upload.execute(png_bytes())

    assert excinfo.value.code == "PAYLOAD_TOO_LARGE"
    assert not store.exists("mem://refs/uploads/upl_0001.png")


def test_project_creation_moves_uploads_under_the_project(container):
    """완료 기준: 화면에서 올린 이미지가 버전의 참조 이미지로 저장된다 (refs/{project_id}/...)."""
    upload = container.upload_reference_image.execute(png_bytes())

    draft = create_draft(container, references=[ReferenceImageInput(upload.image_uri, "cup")])

    stored = container.queries.get_version(draft.version_id).reference_images
    assert [(r.image_uri, r.entity_key) for r in stored] == [
        (f"mem://refs/{draft.project_id}/img_0001.png", "cup")
    ]
    store = container.artifact_store
    assert store.get(stored[0].image_uri) == png_bytes()
    assert store.content_type(stored[0].image_uri) == "image/png"


def test_external_addresses_are_kept_as_they_are(container):
    refs = [
        ReferenceImageInput("https://example.com/style.png"),
        ReferenceImageInput("https://example.com/cup.png", "cup"),
    ]
    draft = create_draft(container, references=refs)
    stored = container.queries.get_version(draft.version_id).reference_images
    assert [r.image_uri for r in stored] == [r.image_uri for r in refs]


@pytest.mark.parametrize(
    "uri",
    [
        "mem://refs/cup.png",  # 업로드를 거치지 않은 저장소 주소
        "mem://clips/ver_0009/intro.mp4",  # 다른 버전의 클립
        "mem://refs/prj_0009/img_0001.png",  # 다른 프로젝트의 참조 이미지
        "mem://refs/uploads/../clips/x.png",  # 업로드처럼 보이지만 모양이 다르다
        # 델타 D05: 업로드도 http·https 도 아닌 주소 — 서버가 읽을 수 없어 생성 단계에서야 실패하고,
        # 응답의 image_uri·구조화 프롬프트에 그대로 실린다
        "file:///etc/passwd",
        "C:/Users/secret.png",
        "../outside.png",
        "javascript:alert(1)",
        "LOCAL://clips/x/intro.mp4",
        " mem://refs/uploads/upl_0001.png",
        "https://",
        "data:image/png;base64,iVBORw0KGgo=",
    ],
)
def test_addresses_other_than_uploads_and_http_are_rejected(container, uri):
    """감사 A-19·델타 D05: 참조 이미지 주소는 올린 이미지의 주소이거나 http·https 주소만 받는다
    (저장 루트 안의 아무 파일이나 참조 이미지로 내려받게 되지 않도록). 아무것도 저장하지 않는다."""
    command = CreateProjectCommand(
        "카페", CAFE_PROMPT, reference_images=(ReferenceImageInput(uri, "cup"),)
    )

    with pytest.raises(ValidationError) as excinfo:
        container.create_project.execute(command)

    assert "올린 이미지" in excinfo.value.message
    with pytest.raises(NotFound):
        container.queries.get_project("prj_0001")


def test_a_missing_upload_is_rejected_and_nothing_is_saved(container):
    gone = "mem://refs/uploads/upl_9999.png"
    command = CreateProjectCommand(
        "카페", CAFE_PROMPT, reference_images=(ReferenceImageInput(gone, "cup"),)
    )

    with pytest.raises(ValidationError) as excinfo:
        container.create_project.execute(command)

    assert gone in excinfo.value.message
    with pytest.raises(NotFound):
        container.queries.get_project("prj_0001")


def test_uploads_work_with_block_ir_input_too(container):
    upload = container.upload_reference_image.execute(jpeg_bytes())
    result = container.create_project.execute(
        CreateProjectCommand(
            "카페 블록",
            "",
            ir=cafe_block_document(),
            reference_images=(ReferenceImageInput(upload.image_uri, "cup"),),
        )
    )
    stored = container.queries.get_version(result.version_id).reference_images
    assert stored[0].image_uri == f"mem://refs/{result.project_id}/img_0001.jpg"


def test_uploaded_reference_is_sent_to_generation_and_copied_into_revisions():
    """완료 기준: 올린 이미지가 생성 요청의 참조로 쓰이고 수정 버전에 복제된다."""
    store = InMemoryArtifactStore()
    generator = FakeVideoGenerator(store)
    container = make_container(artifact_store=store, generator=generator)
    upload = container.upload_reference_image.execute(png_bytes())
    draft = create_draft(container, references=[ReferenceImageInput(upload.image_uri, "cup")])
    claimed = f"mem://refs/{draft.project_id}/img_0001.png"

    generate(container, draft.version_id)

    sent = {r.scene.scene_key: [ref.image_uri for ref in r.references] for r in generator.requests}
    assert sent == {"intro": [claimed], "closeup": [claimed], "outro": []}  # cup 이 나오는 장면만
    revision = container.create_revision.execute(
        CreateRevisionCommand(
            draft.version_id, AttributeChange(entity_key="cup", predicate="color", new_value="teal")
        )
    )
    copied = container.queries.get_version(revision.version_id).reference_images
    assert [(r.image_uri, r.entity_key) for r in copied] == [(claimed, "cup")]
    assert version_of(container, revision.version_id).state.value == "DRAFT"


def test_local_files_are_written_under_the_project_folder(tmp_path):
    store = LocalFileArtifactStore(tmp_path)
    container = make_container(artifact_store=store)
    upload = container.upload_reference_image.execute(png_bytes())
    assert (tmp_path / "refs" / "uploads" / "upl_0001.png").read_bytes() == png_bytes()

    draft = create_draft(container, references=[ReferenceImageInput(upload.image_uri, "cup")])

    assert (tmp_path / "refs" / draft.project_id / "img_0001.png").read_bytes() == png_bytes()
    stored = container.queries.get_version(draft.version_id).reference_images
    assert stored[0].image_uri == f"local://refs/{draft.project_id}/img_0001.png"
