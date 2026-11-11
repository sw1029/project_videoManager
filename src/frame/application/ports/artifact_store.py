"""산출물(영상·클립·추적 자료·참조 이미지) 저장 포트. 구현: 메모리 / 로컬 FS / (향후) Drive·S3.

영상 조회 엔드포인트(GET /api/versions/{id}/video 등)는 이 포트만 보고 응답을 만든다.
- content_type(): 응답의 Content-Type
- local_path(): 파일로 저장된 경우 경로 (HTTP Range 응답으로 영상 탐색을 지원). 그 밖의 저장소는 None.
"""

from __future__ import annotations

import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath

# 플랫폼(Windows 레지스트리 등)에 따라 달라지지 않도록 자주 쓰는 형식은 고정한다
_KNOWN_MEDIA_TYPES: dict[str, str] = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
DEFAULT_MEDIA_TYPE = "application/octet-stream"


def guess_media_type(key_or_uri: str) -> str:
    """키 또는 URI 의 확장자로 미디어 형식을 추정한다."""
    path = key_or_uri.split("://", 1)[-1]
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in _KNOWN_MEDIA_TYPES:
        return _KNOWN_MEDIA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or DEFAULT_MEDIA_TYPE


class ArtifactStore(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        """저장 후 URI 를 돌려준다. 같은 key 에 다시 put 하면 덮어쓴다. content_type 이 없으면 확장자로 추정."""

    @abstractmethod
    def get(self, uri: str) -> bytes:
        """없으면 StorageError."""

    @abstractmethod
    def exists(self, uri: str) -> bool: ...

    def content_type(self, uri: str) -> str:
        """저장된 산출물의 미디어 형식. 기본 구현은 확장자로 추정한다."""
        return guess_media_type(uri)

    def local_path(self, uri: str) -> Path | None:
        """파일로 저장된 산출물이면 경로, 아니면 None (기본)."""
        return None
