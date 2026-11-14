"""산출물 저장소 (ArtifactStore 포트): 클립 · 이미지 · 기록을 주소(URI)로 저장하고 읽는다.

맡는 개념
    저장 키는 규칙을 지켜야 한다 (저장 루트 밖을 가리키는 키는 받지 않는다). 규칙은 포트에 있고
    (application/ports/artifact_store.py 의 check_artifact_key) 두 구현이 같은 계약을 따른다.

밖에서 쓰는 이름
    LocalFileArtifactStore    저장 루트 아래의 파일 (FRAME_ARTIFACT_STORE=local_fs, 주소는 local://<키>)
    InMemoryArtifactStore     프로세스 메모리 (테스트 · 기본 설정, 주소는 mem://<키>)

모듈 지도
    local_fs    로컬 파일 구현 (임시 파일에 쓰고 바꿔 끼운다)
    memory      메모리 구현
"""

from .local_fs import LocalFileArtifactStore
from .memory import InMemoryArtifactStore

__all__ = ["InMemoryArtifactStore", "LocalFileArtifactStore"]
