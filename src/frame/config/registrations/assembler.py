"""최종 영상 조립 (VideoAssembler) 의 등록 — 장면 클립을 이어 영상 하나로 만든다 (US-01)."""

from __future__ import annotations

from frame.adapters.assembler import FakeVideoAssembler, FfmpegVideoAssembler
from frame.adapters.generator.openrouter import output_size
from frame.application.ports import VideoAssembler

from ..registry import AdapterRegistry, BuildContext
from ._dependencies import artifact_store, ffmpeg_path


def register(registry: AdapterRegistry) -> None:
    registry.register(VideoAssembler, "fake", lambda ctx: FakeVideoAssembler(artifact_store(ctx)))
    registry.register(VideoAssembler, "ffmpeg", _ffmpeg)


def _ffmpeg(ctx: BuildContext) -> VideoAssembler:
    """장면 클립을 이어 최종 mp4 를 만든다. 생성기가 정규화한 클립과 같은 규격(크기·fps)을 목표로 한다."""
    s = ctx.settings
    width, height = output_size(s.video_resolution, s.video_aspect_ratio)
    return FfmpegVideoAssembler(
        artifact_store(ctx), ffmpeg_path(ctx), fps=s.video_fps, width=width, height=height
    )
