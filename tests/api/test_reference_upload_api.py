"""#12 POST /api/reference-images — 화면에서 올린 이미지를 프로젝트의 참조 이미지로 쓴다 (multipart/form-data)."""

import pytest
from fastapi.testclient import TestClient

from frame.adapters.generator.fake import FakeVideoGenerator
from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
from frame.config import Settings
from frame.presentation.app import create_app
from tests.support.factories import CAFE_PROMPT, cafe_block_document, dispatcher_of, make_container
from tests.support.images import GIF_BYTES, jpeg_bytes, png_bytes


def _client(**overrides):
    container = make_container(**overrides)
    return TestClient(create_app(container), raise_server_exceptions=False), container


def _upload(client, data: bytes, filename="cup.png", content_type="image/png"):
    return client.post("/api/reference-images", files={"file": (filename, data, content_type)})


def test_upload_returns_the_uri_to_use_in_project_creation():
    client, _ = _client()

    response = _upload(client, png_bytes())

    assert response.status_code == 201, response.text
    assert response.json() == {
        "image_uri": "mem://refs/uploads/upl_0001.png",
        "media_type": "image/png",
        "size_bytes": len(png_bytes()),
    }


def test_uploaded_image_becomes_a_reference_of_the_version_and_its_revisions():
    """완료 기준: 화면에서 올린 이미지가 버전의 참조 이미지로 저장되고, 생성에 쓰이고, 수정 버전에 복제된다."""
    store = InMemoryArtifactStore()
    generator = FakeVideoGenerator(store)
    client, container = _client(artifact_store=store, generator=generator)
    image_uri = _upload(client, png_bytes()).json()["image_uri"]

    created = client.post(
        "/api/projects",
        json={
            "title": "카페",
            "prompt": CAFE_PROMPT,
            "reference_images": [{"image_uri": image_uri, "entity_key": "cup"}],
        },
    ).json()
    dispatcher_of(container).run_all()

    version = client.get(f"/api/versions/{created['version_id']}").json()
    assert version["state"] == "DRAFT"
    [reference] = version["reference_images"]
    assert reference["image_uri"] == f"mem://refs/{created['project_id']}/img_0001.png"
    assert (reference["entity_key"], reference["origin"]) == ("cup", "USER")
    image = client.get(reference["image_url"])
    assert image.status_code == 200
    assert (image.content, image.headers["content-type"]) == (png_bytes(), "image/png")

    assert client.post(f"/api/versions/{version['version_id']}/generations").status_code == 202
    dispatcher_of(container).run_all()
    sent = {r.scene.scene_key: [ref.image_uri for ref in r.references] for r in generator.requests}
    assert sent["intro"] == [reference["image_uri"]] and sent["outro"] == []

    revised = client.post(
        f"/api/versions/{version['version_id']}/revisions",
        json={"entity_key": "cup", "predicate": "color", "new_value": "teal"},
    )
    assert revised.status_code == 201, revised.text
    copied = client.get(f"/api/versions/{revised.json()['version_id']}").json()
    assert [(r["image_uri"], r["entity_key"]) for r in copied["reference_images"]] == [
        (reference["image_uri"], "cup")
    ]
    assert client.get(copied["reference_images"][0]["image_url"]).content == png_bytes()


def test_uploads_can_go_with_block_ir_input():
    client, _ = _client()
    image_uri = _upload(client, jpeg_bytes(), "cup.jpg", "image/jpeg").json()["image_uri"]

    created = client.post(
        "/api/projects",
        json={
            "title": "카페 블록",
            "ir": cafe_block_document(),
            "reference_images": [{"image_uri": image_uri, "entity_key": "cup"}],
        },
    )

    assert created.status_code == 201, created.text
    version = client.get(f"/api/versions/{created.json()['version_id']}").json()
    assert version["reference_images"][0]["image_uri"].endswith("/img_0001.jpg")
    assert version["reference_images"][0]["image_url"]


def test_local_file_store_keeps_uploads_under_refs(tmp_path):
    client, _ = _client(artifact_store=LocalFileArtifactStore(tmp_path))
    image_uri = _upload(client, png_bytes()).json()["image_uri"]
    assert image_uri == "local://refs/uploads/upl_0001.png"

    created = client.post(
        "/api/projects",
        json={
            "title": "카페",
            "ir": cafe_block_document(),
            "reference_images": [{"image_uri": image_uri, "entity_key": "cup"}],
        },
    ).json()

    stored = tmp_path / "refs" / created["project_id"] / "img_0001.png"
    assert stored.read_bytes() == png_bytes()
    version = client.get(f"/api/versions/{created['version_id']}").json()
    image = client.get(version["reference_images"][0]["image_url"])
    assert (image.status_code, image.content) == (200, png_bytes())


def test_the_server_decides_the_format_from_the_content():
    client, _ = _client()
    response = _upload(client, png_bytes(), filename="../../evil.jpg", content_type="image/jpeg")
    assert response.status_code == 201
    assert (
        response.json()["image_uri"] == "mem://refs/uploads/upl_0001.png"
    )  # 이름·표기는 쓰지 않는다
    assert response.json()["media_type"] == "image/png"


def test_unsupported_formats_return_415():
    client, _ = _client()
    response = _upload(client, GIF_BYTES, "cup.gif", "image/gif")
    assert response.status_code == 415
    assert response.json()["error_code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_files_over_the_limit_return_413():
    limit = len(png_bytes()) - 1
    settings = Settings(jobs="recording", persistence="memory", reference_image_max_bytes=limit)
    client, container = _client(settings=settings)

    response = _upload(client, png_bytes())

    assert response.status_code == 413
    assert response.json()["error_code"] == "PAYLOAD_TOO_LARGE"
    assert not container.artifact_store.exists("mem://refs/uploads/upl_0001.png")


def test_bodies_declared_far_over_the_limit_are_refused_before_reading():
    settings = Settings(jobs="recording", persistence="memory", reference_image_max_bytes=1024)
    client, _ = _client(settings=settings)

    response = client.post(
        "/api/reference-images",
        content=b"x" * 10,
        headers={"Content-Type": "multipart/form-data; boundary=x", "Content-Length": "999999"},
    )

    assert response.status_code == 413
    assert response.json()["error_code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.parametrize(
    "files",
    [
        None,  # file 필드가 없다
        {"image": ("cup.png", png_bytes(), "image/png")},  # 다른 이름의 필드
    ],
)
def test_a_missing_file_field_returns_422(files):
    client, _ = _client()
    response = client.post("/api/reference-images", files=files, data={"note": "x"})
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


def test_empty_files_return_422():
    client, _ = _client()
    response = _upload(client, b"")
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


def test_a_missing_upload_is_rejected_when_creating_the_project():
    client, _ = _client()
    response = client.post(
        "/api/projects",
        json={
            "title": "카페",
            "prompt": CAFE_PROMPT,
            "reference_images": [{"image_uri": "mem://refs/uploads/upl_9999.png"}],
        },
    )
    assert response.status_code == 422
    assert "upl_9999" in response.json()["error_message"]


@pytest.mark.parametrize(
    "uri",
    [
        "local://refs/uploads/../../secret.png",
        "local://../secret.png",
        "local://clips/ver_x/intro.mp4",
    ],
)
def test_store_addresses_other_than_uploads_are_rejected(tmp_path, uri):
    """감사 A-19: 이 저장소의 주소는 업로드 결과만 받는다. 저장 루트 밖·다른 버전의 파일을 가리키는 주소는 422."""
    client, _ = _client(artifact_store=LocalFileArtifactStore(tmp_path / "store"))
    (tmp_path / "secret.png").write_bytes(png_bytes())

    response = client.post(
        "/api/projects",
        json={
            "title": "카페",
            "ir": cafe_block_document(),
            "reference_images": [{"image_uri": uri, "entity_key": "cup"}],
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert client.get("/api/projects/prj_0001").status_code == 404


def test_external_addresses_are_kept_but_not_served(tmp_path):
    """외부 주소는 지금처럼 남기되 서버가 내려 주지 않는다 (image_url 없음, 내려받기 404)."""
    client, _ = _client(artifact_store=LocalFileArtifactStore(tmp_path / "store"))

    created = client.post(
        "/api/projects",
        json={
            "title": "카페",
            "ir": cafe_block_document(),
            "reference_images": [{"image_uri": "https://example.com/cup.png", "entity_key": "cup"}],
        },
    ).json()

    version = client.get(f"/api/versions/{created['version_id']}").json()
    [reference] = version["reference_images"]
    assert (reference["image_uri"], reference["image_url"]) == ("https://example.com/cup.png", None)
    path = (
        f"/api/versions/{version['version_id']}/reference-images/{reference['reference_image_id']}"
    )
    assert client.get(path).status_code == 404


def test_uploads_without_a_declared_size_are_refused_before_reading():
    """감사 4: 크기를 밝히지 않은(chunked) 업로드는 본문을 임시 파일에 받기 전에 411 로 거절한다."""
    client, container = _client()

    def body():  # 제너레이터 본문은 Content-Length 없이 chunked 로 간다
        yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\n'
        yield b"Content-Type: image/png\r\n\r\n" + png_bytes() + b"\r\n--x--\r\n"

    response = client.post(
        "/api/reference-images",
        content=body(),
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )

    assert response.status_code == 411
    assert response.json()["error_code"] == "LENGTH_REQUIRED"
    assert not container.artifact_store.exists("mem://refs/uploads/upl_0001.png")
