"""옛 경로 (호환용). 클립 정규화는 .normalizer 에, ffmpeg 찾기와 클립 검사는 frame.adapters.shared.media 에,
최종 영상 조립기는 frame.adapters.assembler 에 있다 — 새 코드는 그쪽을 import 한다 (ADR-0031).

작업 중인 브랜치가 이 경로를 쓰고 있어 남겨 둔다. 그 브랜치들이 dev 에 들어간 뒤 지운다.
"""

from frame.adapters.shared.media.ffmpeg import resolve_ffmpeg
from frame.adapters.shared.media.probe import VideoProbe, probe_video

from .normalizer import FfmpegVideoNormalizer, NormalizedVideo, VideoNormalizer

__all__ = [
    "FfmpegVideoNormalizer",
    "NormalizedVideo",
    "VideoNormalizer",
    "VideoProbe",
    "probe_video",
    "resolve_ffmpeg",
]
