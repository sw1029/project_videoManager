"""장면 클립 생성 (VideoGenerator) 의 등록 — US-01. 재시도·시간 제한·캐시는 컨테이너가 설정에 따라 그 위에 씌운다."""

from __future__ import annotations

from frame.adapters.generator import FakeVideoGenerator, OpenRouterVideoGenerator
from frame.adapters.generator.ffmpeg import FfmpegVideoNormalizer
from frame.adapters.generator.openrouter import resolve_openrouter_key
from frame.application.ports import VideoGenerator

from ..registry import AdapterRegistry, BuildContext
from ._dependencies import artifact_store, ffmpeg_path, skeleton


def register(registry: AdapterRegistry) -> None:
    registry.register(VideoGenerator, "fake", lambda ctx: FakeVideoGenerator(artifact_store(ctx)))
    # 로컬 추론(DiffusersVideoGenerator)은 뼈대뿐이다. 구현하면 여기서 그 클래스를 만든다
    registry.register(
        VideoGenerator,
        "diffusers",
        skeleton("FRAME_GENERATOR", "diffusers", working="fake · external_api"),
    )
    registry.register(VideoGenerator, "external_api", _openrouter)


def _openrouter(ctx: BuildContext) -> VideoGenerator:
    """OpenRouter 영상 API (ADR-0011). 키나 ffmpeg 가 없으면 기동 시점에 ConfigurationError."""
    s = ctx.settings
    return OpenRouterVideoGenerator(
        store=artifact_store(ctx),
        api_key=resolve_openrouter_key(key_file=s.video_key_file),
        model=s.video_model,
        resolution=s.video_resolution,
        aspect_ratio=s.video_aspect_ratio,
        fps=s.video_fps,
        base_url=s.video_base_url,
        timeout_seconds=s.video_timeout_s,
        poll_interval_seconds=s.video_poll_interval_s,
        normalizer=FfmpegVideoNormalizer(ffmpeg_path(ctx)),
        edit_mode=s.video_edit_mode,
        edit_model=s.video_edit_model,
        edit_parent_wait_seconds=s.video_edit_parent_wait_s,
    )
