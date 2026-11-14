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


# 키는 `/` 로 나눈 상대 경로다. 다른 자리를 가리키거나 OS 에 따라 다른 파일이 되는 키는 받지 않는다
UNSAFE_KEYS = [
    "",
    "../outside.json",
    "clips/../../outside.mp4",
    "clips/ver_attacker/../ver_victim/intro.mp4",  # 저장 루트 안의 다른 버전 (감사 A-01)
    "clips/./intro.mp4",
    "clips//intro.mp4",
    "/clips/intro.mp4",
    "clips/intro.mp4/",
    "clips\\ver\\intro.mp4",
    "C:/clips/intro.mp4",
    "clips/intro:stream.mp4",
    "clips/intro.mp4\n",
    "clips/intro\x00.mp4",
    "clips/intro.",
    "clips/intro ",
]


@pytest.mark.parametrize("key", UNSAFE_KEYS)
def test_unsafe_keys_are_refused(store, key):
    with pytest.raises(StorageError):
        store.put(key, b"x")
    with pytest.raises(StorageError):
        store.uri_for(key)


def test_a_key_cannot_overwrite_another_versions_clip(store):
    """장면 키에 `../` 가 들어와도 다른 버전의 클립을 덮어쓰지 못한다 (감사 A-01)."""
    victim = store.put("clips/ver_victim/intro.mp4", b"victim")

    with pytest.raises(StorageError):
        store.put("clips/ver_attacker/../ver_victim/intro.mp4", b"attacker")

    assert store.get(victim) == b"victim"


def test_keys_may_use_other_scripts_and_nested_folders(store):
    """형식은 도메인이 정한다. 저장소는 다른 자리를 가리키는 키만 막는다."""
    for key in (
        "clips/ver_1/장면 하나.mp4",
        "references/identity/ab12/cup-1a2b3c.png",
        "a/b/c/d.json",
    ):
        uri = store.put(key, b"x")
        assert store.exists(uri) and store.uri_for(key) == uri


def test_guess_media_type_defaults_to_octet_stream():
    assert guess_media_type("blob/without-extension") == "application/octet-stream"
    assert guess_media_type("mem://videos/v.webm") == "video/webm"
