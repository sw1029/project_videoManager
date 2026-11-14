"""참조 이미지 업로드 (#12, Endpoint Catalog "POST /api/projects 는 요구사항·참조 이미지를 접수").

1. 올리기 (UploadReferenceImage): 형식(PNG·JPEG·WebP, 파일 내용의 첫 바이트로 판별)·크기를 검사하고
   산출물 저장소의 임시 자리 refs/uploads/{upload_id}.{ext} 에 저장한 뒤 URI 를 돌려준다.
   화면은 그 URI 를 POST /api/projects 의 reference_images[].image_uri 에 쓴다.
2. 주소 정하기: 프로젝트를 만들 때 참조 이미지 URI 를 두 단계로 정한다 — 모두 검사한 뒤에(check_reference_uri)
   옮긴다(place_reference). 뒤의 참조가 거부되면 앞의 것의 복사본이 남지 않게 한다 (델타 D03).
   - 임시 자리의 업로드면 refs/{project_id}/{reference_image_id}.{ext} 로 복사하고 그 URI 를 남긴다.
   - http·https 주소는 그대로 둔다.
   - 그 밖의 주소는 받지 않는다 (422). 서버 저장소의 다른 주소(local://·mem://)로 저장 루트 안의 다른 파일
     (다른 버전의 클립·다른 프로젝트의 이미지·캐시)을 참조 이미지로 내려받게 되지 않도록 (감사 A-19),
     서버가 읽을 수 없는 주소(file·경로·javascript 등)가 생성 단계에서야 실패하거나
     응답에 그대로 실리지 않도록 (델타 D05).

저장 키는 서버가 만든 id 와 판별한 형식의 확장자로만 짓는다 (올린 쪽의 파일 이름·형식 표기는 쓰지 않는다).
임시 자리의 원본은 지우지 않는다 (저장소 포트에 삭제가 없다). 정리는 따로 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from frame.domain.errors import PayloadTooLarge, UnsupportedMediaType, ValidationError

from ..errors import StorageError
from ..ports.artifact_store import ArtifactStore
from ..ports.common import IdGenerator

UPLOAD_PREFIX = "refs/uploads/"
# 10 MiB. 생성기는 참조 이미지를 data URI 로 보내므로 작을수록 좋다
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
EXTENSIONS: dict[str, str] = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
# WebP 를 알아보는 데 필요한 머리의 길이: "RIFF" + 크기 4바이트 + "WEBP"
_WEBP_HEADER_BYTES = 12
_UPLOAD_NAME = re.compile(r"[A-Za-z0-9_-]{1,100}\.(?:png|jpg|webp)")
EXTERNAL_SCHEMES = frozenset(
    {"http", "https"}
)  # 업로드가 아닌 참조 이미지가 쓸 수 있는 주소 (델타 D05)


def payload_too_large(max_bytes: int) -> PayloadTooLarge:
    """크기 상한을 넘은 업로드의 오류 (본문을 다 읽기 전에 거절하는 HTTP 경로도 같은 문구를 쓴다)."""
    return PayloadTooLarge(f"참조 이미지는 {max_bytes:,} 바이트까지 올릴 수 있습니다")


def sniff_image_type(data: bytes) -> str | None:
    """파일 내용의 첫 바이트로 형식을 판별한다. 받을 수 있는 형식(PNG·JPEG·WebP)이 아니면 None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= _WEBP_HEADER_BYTES and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
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


@dataclass(frozen=True)
class CheckedReference:
    """검사를 통과한 참조 이미지 주소. upload_name 이 있으면 올린 이미지(프로젝트 자리로 옮길 것)다."""

    image_uri: str
    upload_name: str | None = None


def check_reference_uri(store: ArtifactStore, image_uri: str) -> CheckedReference:
    """1단계: 저장소에 아무것도 쓰지 않고 참조 이미지 주소를 검사한다. 쓸 수 없는 주소는 ValidationError (422).

    참조를 모두 검사한 뒤에 옮겨야, 뒤의 참조가 거부될 때 앞의 것의 복사본이 남지 않는다 (델타 D03).
    """
    name = _upload_name(store, image_uri)
    if name is not None:
        if not store.exists(image_uri):
            raise _missing_upload(image_uri)
        return CheckedReference(image_uri, upload_name=name)
    if not _is_external_address(image_uri):
        raise ValidationError(
            "참조 이미지 주소는 올린 이미지의 주소(업로드 응답의 image_uri)이거나 "
            f"http·https 주소여야 합니다: {image_uri}"
        )
    return CheckedReference(image_uri)


def place_reference(
    store: ArtifactStore, checked: CheckedReference, *, project_id: str, reference_image_id: str
) -> str:
    """2단계: 검사한 참조를 프로젝트에 둔다. 올린 이미지는 refs/{project_id}/{reference_image_id}.{ext} 로 복사한 URI,
    외부 주소는 받은 그대로."""
    if checked.upload_name is None:
        return checked.image_uri
    try:
        data = store.get(checked.image_uri)
    except StorageError as exc:  # 검사와 복사 사이에 없어졌다
        raise _missing_upload(checked.image_uri) from exc
    key = f"refs/{project_id}/{reference_image_id}{PurePosixPath(checked.upload_name).suffix}"
    return store.put(key, data, content_type=store.content_type(checked.image_uri))


def _missing_upload(image_uri: str) -> ValidationError:
    return ValidationError(f"올린 참조 이미지를 찾을 수 없습니다: {image_uri} (다시 올려 주세요)")


def _is_external_address(uri: str) -> bool:
    """호스트가 있는 http·https 주소인가 (앞뒤 공백 없이)."""
    parts = urlsplit(uri)
    return uri == uri.strip() and parts.scheme.lower() in EXTERNAL_SCHEMES and bool(parts.netloc)


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
