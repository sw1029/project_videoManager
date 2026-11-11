from __future__ import annotations

from pathlib import Path

from frame.application.errors import StorageError
from frame.application.ports.artifact_store import ArtifactStore


class LocalFileArtifactStore(ArtifactStore):
    """URI 형식: local://<root 기준 상대 경로>. (Drive/S3 로 바꿔도 URI 만 달라진다)

    미디어 형식은 따로 저장하지 않고 확장자로 추정한다. 영상은 .mp4 처럼 확장자를 붙여 저장한다.
    """

    SCHEME = "local://"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"{self.SCHEME}{key}"

    def get(self, uri: str) -> bytes:
        path = self._path_for(self._key_of(uri))
        if not path.is_file():
            raise StorageError(f"산출물이 없습니다: {uri}")
        return path.read_bytes()

    def exists(self, uri: str) -> bool:
        return self._path_for(self._key_of(uri)).is_file()

    def local_path(self, uri: str) -> Path | None:
        try:
            path = self._path_for(self._key_of(uri))
        except StorageError:
            return None
        return path if path.is_file() else None

    def _key_of(self, uri: str) -> str:
        if not uri.startswith(self.SCHEME):
            raise StorageError(f"지원하지 않는 URI: {uri}")
        return uri[len(self.SCHEME) :]

    def _path_for(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root not in path.parents and path != self._root:
            raise StorageError(f"저장 루트를 벗어난 키: {key}")
        return path
