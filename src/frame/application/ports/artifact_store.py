"""산출물(영상·클립·추적 자료·참조 이미지) 저장 포트. 구현: 메모리 / 로컬 FS / (향후) Drive·S3."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ArtifactStore(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """저장 후 URI 를 돌려준다. 같은 key 에 다시 put 하면 덮어쓴다."""

    @abstractmethod
    def get(self, uri: str) -> bytes: ...

    @abstractmethod
    def exists(self, uri: str) -> bool: ...
