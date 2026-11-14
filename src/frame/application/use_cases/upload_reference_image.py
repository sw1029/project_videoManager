"""참조 이미지 업로드 (#12, Endpoint Catalog "POST /api/projects 는 요구사항·참조 이미지를 접수").

1. 올리기 (UploadReferenceImage): 형식(PNG·JPEG·WebP, 파일 내용의 첫 바이트로 판별)·크기를 검사하고
   산출물 저장소의 임시 자리 refs/uploads/{upload_id}.{ext} 에 저장한 뒤 URI 를 돌려준다.
   화면은 그 URI 를 POST /api/projects 의 reference_images[].image_uri 에 쓴다.
2. 옮기기 (claim_uploaded_reference): 프로젝트를 만들 때 임시 자리의 이미지를
   refs/{project_id}/{reference_image_id}.{ext} 로 복사하고 그 URI 를 버전의 참조 이미지로 남긴다.
   업로드가 아닌 URI(외부 주소·직접 지정한 저장소 URI)는 그대로 둔다.

저장 키는 서버가 만든 id 와 판별한 형식의 확장자로만 짓는다 (올린 쪽의 파일 이름·형식 표기는 쓰지 않는다).
임시 자리의 원본은 지우지 않는다 (저장소 포트에 삭제가 없다). 정리는 따로 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from frame.domain.errors import PayloadTooLarge, UnsupportedMediaType, ValidationError

from ..errors import StorageError
from ..ports.artifact_store import ArtifactStore
from ..ports.common import IdGenerator

UPLOAD_PREFIX = "refs/uploads/"
# 10 MiB. 생성기는 참조 이미지를 data URI 로 보내므로 작을수록 좋다
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
EXTENSIONS: dict[str, str] = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_UPLOAD_NAME = re.compile(r"[A-Za-z0-9_-]{1,100}\.(?:png|jpg|webp)")


def payload_too_large(max_bytes: int) -> PayloadTooLarge:
    """크기 상한을 넘은 업로드의 오류 (본문을 다 읽기 전에 거절하는 HTTP 경로도 같은 문구를 쓴다)."""
    return PayloadTooLarge(f"참조 이미지는 {max_bytes:,} 바이트까지 올릴 수 있습니다")


def sniff_image_type(data: bytes) -> str | None:
    """파일 내용의 첫 바이트로 형식을 판별한다. 받을 수 있는 형식(PNG·JPEG·WebP)이 아니면 None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True)
class UploadedReferenceImage:
    image_uri: str  # POST /api/projects 의 reference_images[].image_uri 에 그대로 쓴다
    media_type: str
    size_bytes: int


class UploadReferenceImage:
    def __init__(
        self, store: ArtifactStore, ids: IdGenerator, *, max_bytes: int = DEFAULT_MAX_BYTES
    ) -> None:
        self._store = store
        self._ids = ids
        self.max_bytes = max_bytes

    def execute(self, data: bytes) -> UploadedReferenceImage:
        if not data:
            raise ValidationError("빈 파일입니다")
        if len(data) > self.max_bytes:
            raise payload_too_large(self.max_bytes)
        media_type = sniff_image_type(data)
        if media_type is None:
            raise UnsupportedMediaType("참조 이미지는 PNG·JPEG·WebP 만 올릴 수 있습니다")
        key = f"{UPLOAD_PREFIX}{self._ids.new_id('upl')}{EXTENSIONS[media_type]}"
        uri = self._store.put(key, data, content_type=media_type)
        return UploadedReferenceImage(image_uri=uri, media_type=media_type, size_bytes=len(data))


def claim_uploaded_reference(
    store: ArtifactStore, image_uri: str, *, project_id: str, reference_image_id: str
) -> str:
    """임시 자리에 올린 이미지면 refs/{project_id}/{reference_image_id}.{ext} 로 복사해 그 URI 를,
    아니면 받은 URI 를 그대로 돌려준다."""
    name = _upload_name(store, image_uri)
    if name is None:
        return image_uri
    try:
        data = store.get(image_uri)
    except StorageError as exc:
        raise ValidationError(
            f"올린 참조 이미지를 찾을 수 없습니다: {image_uri} (다시 올려 주세요)"
        ) from exc
    key = f"refs/{project_id}/{reference_image_id}{PurePosixPath(name).suffix}"
    return store.put(key, data, content_type=store.content_type(image_uri))


def _upload_name(store: ArtifactStore, uri: str) -> str | None:
    """이 저장소의 임시 자리에 있는 업로드의 URI 면 파일 이름, 아니면 None.

    이름은 서버가 짓는 모양(영문·숫자·_- 와 받는 확장자)만 인정하고, 그 이름으로 저장소가 만드는 URI 와 같아야 한다.
    URI 를 미리 계산할 수 없는 저장소(uri_for 가 None)에서는 옮기지 않는다.
    """
    marker = uri.rfind(UPLOAD_PREFIX)
    if marker < 0:
        return None
    name = uri[marker + len(UPLOAD_PREFIX) :]
    if not _UPLOAD_NAME.fullmatch(name):
        return None
    if store.uri_for(f"{UPLOAD_PREFIX}{name}") != uri:
        return None
    return name
