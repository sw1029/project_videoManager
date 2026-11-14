"""로컬 파일 산출물 저장소: 저장 루트 아래의 파일로 둔다 (URI 는 local://<키>).

쓰기는 같은 폴더의 임시 파일에 다 쓴 뒤 바꿔 끼운다. 작업 원장·생성 캐시의 기록이 여기에 있어서,
쓰는 도중 끊기거나 다른 스레드가 읽어도 잘린 기록이 보이면 안 된다 (잘린 기록은 '없음' 으로 읽혀 다시 과금된다).
"""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from frame.application.errors import StorageError
from frame.application.ports.artifact_store import ArtifactStore, check_artifact_key

# Windows 는 다른 스레드·프로세스가 파일을 열고 있는 동안 바꿔 끼우기·읽기를 거절한다 (PermissionError).
# 그런 순간은 짧으므로 잠깐씩 기다리며 다시 한다. 이만큼 기다려도 잠겨 있으면 그만둔다
_BUSY_WAIT_S = 1.0
_BUSY_PAUSE_S = 0.005

_T = TypeVar("_T")


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
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            _while_busy(lambda: temporary.replace(path))
        except OSError as exc:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise StorageError(f"산출물을 쓰지 못했습니다: {key} ({_reason(exc)})") from exc
        return f"{self.SCHEME}{key}"

    def get(self, uri: str) -> bytes:
        path = self._path_for(self._key_of(uri))
        if not path.is_file():
            raise StorageError(f"산출물이 없습니다: {uri}")
        try:
            return _while_busy(path.read_bytes)
        except OSError as exc:
            raise StorageError(f"산출물을 읽지 못했습니다: {uri} ({_reason(exc)})") from exc

    def exists(self, uri: str) -> bool:
        try:
            return self._path_for(self._key_of(uri)).is_file()
        except StorageError:  # 다른 곳의 주소나 규칙에 맞지 않는 URI: 이 저장소에는 없다
            return False

    def uri_for(self, key: str) -> str:
        self._path_for(key)  # 규칙에 맞지 않거나 저장 루트를 벗어나는 키는 StorageError
        return f"{self.SCHEME}{key}"

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
        check_artifact_key(key)  # `..` 로 저장 루트 안의 다른 폴더를 가리키는 키도 여기서 막힌다
        path = (self._root / key).resolve()
        if self._root not in path.parents and path != self._root:
            raise StorageError(f"저장 루트를 벗어난 키: {key}")
        return path


def _while_busy(action: Callable[[], _T]) -> _T:
    """파일이 잠깐 잠겨 있으면(PermissionError) 기다렸다 다시 한다. 끝까지 잠겨 있으면 그 오류를 올린다."""
    deadline = time.monotonic() + _BUSY_WAIT_S
    pause = _BUSY_PAUSE_S
    while True:
        try:
            return action()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(pause)
            pause = min(pause * 2, 0.05)


def _reason(exc: OSError) -> str:
    """오류의 종류만 남긴다. 서버의 경로가 든 원문은 싣지 않는다 (감사 A-29)."""
    return f"{type(exc).__name__}: {exc.strerror or '입출력 오류'}"
