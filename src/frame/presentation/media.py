"""저장소 URI 를 HTTP 응답으로 바꾼다.

파일 저장소(local_fs)는 FileResponse 로 내려 HTTP Range(영상 탐색)를 지원하고, 그 밖의 저장소는 바이트로 내린다.
버전에 연결된 URI 만 내려주므로(라우터가 버전·장면으로 조회) 임의 경로 접근은 불가능하다.
"""

from __future__ import annotations

from fastapi import Response
from fastapi.responses import FileResponse

from frame.application.errors import StorageError
from frame.application.ports.artifact_store import ArtifactStore
from frame.domain.errors import MediaNotAvailable


def artifact_response(store: ArtifactStore, uri: str) -> Response:
    media_type = store.content_type(uri)
    path = store.local_path(uri)
    if path is not None:
        return FileResponse(path, media_type=media_type)
    try:
        data = store.get(uri)
    except StorageError as exc:
        raise MediaNotAvailable("산출물 파일을 찾을 수 없습니다") from exc
    return Response(content=data, media_type=media_type)
