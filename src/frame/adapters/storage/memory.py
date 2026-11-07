from __future__ import annotations

from frame.application.errors import StorageError
from frame.application.ports.artifact_store import ArtifactStore


class InMemoryArtifactStore(ArtifactStore):
    SCHEME = "mem://"

    def __init__(self) -> None:
        self._blobs: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        uri = f"{self.SCHEME}{key}"
        self._blobs[uri] = (bytes(data), content_type)
        return uri

    def get(self, uri: str) -> bytes:
        try:
            return self._blobs[uri][0]
        except KeyError as exc:
            raise StorageError(f"산출물이 없습니다: {uri}") from exc

    def exists(self, uri: str) -> bool:
        return uri in self._blobs
