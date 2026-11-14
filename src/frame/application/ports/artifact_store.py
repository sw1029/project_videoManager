"""산출물(영상·클립·추적 자료·참조 이미지) 저장 포트. 구현: 메모리 / 로컬 FS / (향후) Drive·S3.

영상 조회 엔드포인트(GET /api/versions/{id}/video 등)는 이 포트만 보고 응답을 만든다.
- content_type(): 응답의 Content-Type
- local_path(): 파일로 저장된 경우 경로 (HTTP Range 응답으로 영상 탐색을 지원). 그 밖의 저장소는 None.

저장 키의 규칙은 check_artifact_key 에 있다. 객체·장면 키처럼 밖에서 온 글자가 키에 들어가므로,
구현은 put·uri_for 에서 이 규칙을 지켜 다른 자리를 가리키는 키를 받지 않는다.
"""

from __future__ import annotations

import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath

from ..errors import StorageError

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


def check_artifact_key(key: str) -> None:
    """저장 키의 규칙을 어기면 StorageError.

    키는 `/` 로 나눈 상대 경로다. 조각은 비어 있지 않고 `.`·`..` 이 아니며, 역슬래시·콜론·제어 문자를 쓰지 않고,
    점이나 공백으로 끝나지 않는다 (Windows 는 이름 끝의 점·공백을 떼어 다른 키와 같은 파일로 만든다).
    글자의 종류는 묻지 않는다: 객체·장면 키의 형식은 도메인이 정하고, 저장소는 다른 자리를 가리키는 키만 막는다.
    """
    if not isinstance(key, str) or not key:
        raise StorageError("저장 키가 비어 있습니다")
    if "\\" in key or ":" in key or any(ord(char) < 32 or ord(char) == 127 for char in key):
        raise StorageError(f"저장 키에 쓸 수 없는 글자가 있습니다: {key!r}")
    for part in key.split("/"):
        if part in ("", ".", "..") or part[-1] in ". ":
            raise StorageError(f"저장 키가 다른 자리를 가리키거나 조각이 비어 있습니다: {key!r}")


class ArtifactStore(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        """저장 후 URI 를 돌려준다. 같은 key 에 다시 put 하면 덮어쓴다. content_type 이 없으면 확장자로 추정.

        규칙에 맞지 않는 키(check_artifact_key)는 StorageError.
        """

    @abstractmethod
    def get(self, uri: str) -> bytes:
        """없거나 이 저장소의 주소가 아니면 StorageError."""

    @abstractmethod
    def exists(self, uri: str) -> bool:
        """이 저장소에 있으면 참. 다른 곳의 주소(외부 URL, 다른 저장소의 URI)와 규칙에 맞지 않는 URI 는
        예외가 아니라 거짓이다 — 부르는 쪽이 저장소 종류에 따라 다르게 동작하지 않게 한다.
        """

    def content_type(self, uri: str) -> str:
        """저장된 산출물의 미디어 형식. 기본 구현은 확장자로 추정한다."""
        return guess_media_type(uri)

    def local_path(self, uri: str) -> Path | None:
        """파일로 저장된 산출물이면 경로, 아니면 None (기본)."""
        return None

    def uri_for(self, key: str) -> str | None:
        """key 로 put 했을 때(하기 전에도)의 URI. 생성 캐시·작업 원장처럼 키로 바로 찾아 읽는 색인이 쓴다.

        URI 를 미리 계산할 수 없는 저장소(업로드 뒤 id 를 받는 원격 저장소 등)는 None (기본).
        그런 저장소에서는 색인을 프로세스 메모리에만 둔다 (ADR-0013).
        규칙에 맞지 않는 키(check_artifact_key)는 StorageError.
        """
        return None
