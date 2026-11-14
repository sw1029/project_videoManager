"""옛 경로 (호환용). 구현은 frame.adapters.shared.media 에 있다 — 새 코드는 그쪽을 import 한다 (ADR-0031).

프레임 뽑기는 shared.media.frames, 크기는 shared.media.probe, SSIM 은 shared.media.similarity,
ffmpeg 찾기·실행과 VideoToolError 는 shared.media.ffmpeg 다.
작업 중인 브랜치가 이 경로를 쓰고 있어 남겨 둔다. 그 브랜치들이 dev 에 들어간 뒤 지운다.
"""

from frame.adapters.shared.media.ffmpeg import VideoToolError, require_ffmpeg
from frame.adapters.shared.media.frames import Box, Image, extract_frame
from frame.adapters.shared.media.probe import video_size
from frame.adapters.shared.media.similarity import ssim

__all__ = [
    "Box",
    "Image",
    "VideoToolError",
    "extract_frame",
    "require_ffmpeg",
    "ssim",
    "video_size",
]
