"""ArtifactStore 계약: 메모리·로컬 파일 구현이 같은 규칙을 따른다 (영상 조회 엔드포인트의 전제)."""

import pytest

from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
from frame.application.errors import StorageError
from frame.application.ports.artifact_store import guess_media_type


@pytest.fixture(params=["memory", "local_fs"])
def store(request, tmp_path):
    return (
        InMemoryArtifactStore() if request.param == "memory" else LocalFileArtifactStore(tmp_path)
    )


def test_put_get_exists_roundtrip(store):
    uri = store.put("clips/v1/intro.json", b'{"a": 1}', "application/json")
    assert store.exists(uri)
    assert store.get(uri) == b'{"a": 1}'
    store.put("clips/v1/intro.json", b"{}", "application/json")
    assert store.get(uri) == b"{}"


def test_uri_for_addresses_a_key_before_and_after_put(store):
    """생성 캐시·작업 원장처럼 키로 바로 찾아 읽는 색인이 쓴다 (재시작 뒤에도 같은 URI)."""
    uri = store.uri_for("cache/clips/abc.json")
    assert uri is not None and not store.exists(uri)
    assert store.put("cache/clips/abc.json", b"{}") == uri
    assert store.exists(uri) and store.get(uri) == b"{}"


def test_missing_artifact_raises_storage_error(store):
    uri = store.put("clips/v1/intro.json", b"{}", "application/json")
    missing = uri.replace("intro", "outro")
    assert not store.exists(missing)
    with pytest.raises(StorageError):
        store.get(missing)


@pytest.mark.parametrize(
    ("key", "media_type"),
    [
        ("videos/v.mp4", "video/mp4"),
        ("clips/a.json", "application/json"),
        ("refs/a.png", "image/png"),
    ],
)
def test_content_type_follows_extension(store, key, media_type):
    uri = store.put(key, b"x")
    assert store.content_type(uri) == media_type


def test_local_path_only_for_file_backed_store(tmp_path):
    local = LocalFileArtifactStore(tmp_path)
    uri = local.put("videos/v.mp4", b"0123")
    path = local.local_path(uri)
    assert path is not None and path.read_bytes() == b"0123"
    assert local.local_path(uri.replace("v.mp4", "none.mp4")) is None
    memory = InMemoryArtifactStore()
    assert memory.local_path(memory.put("videos/v.mp4", b"x")) is None


def test_local_store_refuses_keys_outside_its_root(tmp_path):
    with pytest.raises(StorageError):
        LocalFileArtifactStore(tmp_path / "root").uri_for("../outside.json")


def test_guess_media_type_defaults_to_octet_stream():
    assert guess_media_type("blob/without-extension") == "application/octet-stream"
    assert guess_media_type("mem://videos/v.webm") == "video/webm"
