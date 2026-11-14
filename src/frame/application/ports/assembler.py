"""최종 영상 조립 포트 (US-01): 장면 클립을 순서대로 이어 최종 영상 하나로 만든다.

구현: Fake(이은 순서를 적은 결과 정보를 저장), ffmpeg(concat - 규격이 같으면 스트림 복사, 다르면 다시 인코딩).
생성 포트(generator.py)와 다른 포트다. 예전 경로를 위해 generator.py 도 이 이름을 내보낸다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from frame.domain.artifacts import GeneratedClip, VideoArtifact

__all__ = ["VideoAssembler"]


class VideoAssembler(ABC):
    """계약
    - 클립을 받은 순서대로 잇는다. 영상의 duration_ms 는 클립 duration_ms 의 합이다.
    - 실패는 GenerationError(retryable=False) 로 알린다 (이을 클립이 없다, 클립이 영상이 아니다, 조립 실패).
    """

    @abstractmethod
    def assemble(self, version_id: str, clips: Sequence[GeneratedClip]) -> VideoArtifact: ...
