"""POST /api/reference-images (#12): 참조 이미지를 multipart/form-data 의 file 필드로 올린다.

돌려준 image_uri 를 POST /api/projects 의 reference_images 에 쓰면, 프로젝트를 만들 때 서버가 refs/{project_id}/ 로 옮겨
버전의 참조 이미지로 남긴다 (application/use_cases/upload_reference_image.py). 형식은 파일 내용으로 판별하고
(PNG·JPEG·WebP), 크기 상한은 FRAME_REFERENCE_IMAGE_MAX_BYTES 다.

크기 상한은 두 번 본다.
1. 본문을 받기 전에 선언된 크기(Content-Length)를 본다. 없으면 411, 상한(과 multipart 머리글 여유)을 분명히 넘으면 413.
   FastAPI 는 의존성보다 먼저 본문을 다 받아 임시 파일에 쓰므로, 크기를 밝히지 않은(chunked) 요청을 받아 주면
   요청 하나가 임시 디스크를 얼마든지 채울 수 있다 (감사 4). 브라우저의 FormData 업로드는 늘 Content-Length 를 보낸다.
2. 파일을 상한 + 1 바이트까지만 읽어 유스케이스가 판단한다.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.routing import APIRoute

from frame.application.use_cases.upload_reference_image import payload_too_large
from frame.config.container import Container

from ..dependencies import get_container
from ..rate_limit import enforce_rate_limit
from ..schemas import ErrorOut, ReferenceImageUploadOut

MULTIPART_OVERHEAD_BYTES = 64 * 1024  # 경계·파트 머리글에 주는 여유


class _DeclaredSizeLimitRoute(APIRoute):
    """multipart 본문을 다 받아 임시 파일로 쓰기 전에, 선언된 크기(Content-Length)로 먼저 거른다."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def limited(request: Request) -> Response:
            container: Container = request.app.state.container
            limit = container.upload_reference_image.max_bytes
            declared = request.headers.get("content-length", "")
            if not declared.isdigit():
                raise HTTPException(
                    status.HTTP_411_LENGTH_REQUIRED,
                    "참조 이미지 업로드는 크기(Content-Length)를 밝혀야 합니다",
                )
            if int(declared) > limit + MULTIPART_OVERHEAD_BYTES:
                raise payload_too_large(limit)
            return await handler(request)

        return limited


router = APIRouter(
    prefix="/reference-images", tags=["reference-images"], route_class=_DeclaredSizeLimitRoute
)
ContainerDep = Annotated[Container, Depends(get_container)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ReferenceImageUploadOut,
    responses={
        411: {
            "model": ErrorOut,
            "description": "LENGTH_REQUIRED (Content-Length 없음, chunked 업로드)",
        },
        413: {
            "model": ErrorOut,
            "description": "PAYLOAD_TOO_LARGE (FRAME_REFERENCE_IMAGE_MAX_BYTES, 기본 10 MiB)",
        },
        415: {"model": ErrorOut, "description": "UNSUPPORTED_MEDIA_TYPE (PNG·JPEG·WebP 만)"},
        422: {"model": ErrorOut, "description": "VALIDATION_ERROR (file 필드 없음·빈 파일)"},
        429: {"model": ErrorOut},
    },
    dependencies=[Depends(enforce_rate_limit)],
    summary="참조 이미지를 올린다 (multipart/form-data 의 file). 돌려준 image_uri 를 "
    "POST /api/projects 의 reference_images 에 쓴다",
)
def upload_reference_image(
    file: Annotated[
        UploadFile, File(description="PNG·JPEG·WebP 이미지. 형식은 파일 내용으로 판별한다")
    ],
    container: ContainerDep,
) -> ReferenceImageUploadOut:
    upload = container.upload_reference_image
    data = file.file.read(upload.max_bytes + 1)  # 상한을 넘는지만 알면 되므로 그 이상은 읽지 않는다
    result = upload.execute(data)
    return ReferenceImageUploadOut(
        image_uri=result.image_uri, media_type=result.media_type, size_bytes=result.size_bytes
    )
