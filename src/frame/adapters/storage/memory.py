from __future__ import annotations

from frame.application.errors import StorageError
from frame.application.ports.artifact_store import ArtifactStore, guess_media_type


class InMemoryArtifactStore(ArtifactStore):
    SCHEME = "mem://"

    def __init__(self) -> None:
        self._blobs: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        uri = f"{self.SCHEME}{key}"
        self._blobs[uri] = (bytes(data), content_type or guess_media_type(key))
        return uri

    def get(self, uri: str) -> bytes:
        try:
            return self._blobs[uri][0]
        except KeyError as exc:
            raise StorageError(f"산출물이 없습니다: {uri}") from exc

    def exists(self, uri: str) -> bool:
        return uri in self._blobs

    def content_type(self, uri: str) -> str:
        blob = self._blobs.get(uri)
        return blob[1] if blob is not None else guess_media_type(uri)
