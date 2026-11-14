"""옛 경로의 호환 모듈 (ADR-0031).

공용 모듈을 adapters/shared/ 로 옮기면서 옛 경로에는 이름만 다시 내보내는 모듈을 남겼다. 작업 중인 브랜치가
옛 경로로 import 하는 이름이 새 자리의 것과 같은 객체여야 한다 (isinstance·except 가 그대로 맞는다).
"""

from __future__ import annotations

import importlib

import pytest

# (옛 경로, 이름) -> 구현이 있는 새 모듈
MOVED = [
    ("frame.adapters.common", "UuidIdGenerator", "frame.adapters.shared.runtime"),
    ("frame.adapters.common", "SequentialIdGenerator", "frame.adapters.shared.runtime"),
    ("frame.adapters.common", "SystemClock", "frame.adapters.shared.runtime"),
    ("frame.adapters.common", "FixedClock", "frame.adapters.shared.runtime"),
    ("frame.adapters.http_transport", "HttpTransport", "frame.adapters.shared.http"),
    ("frame.adapters.http_transport", "HttpResponse", "frame.adapters.shared.http"),
    ("frame.adapters.http_transport", "TransportError", "frame.adapters.shared.http"),
    ("frame.adapters.http_transport", "UrllibTransport", "frame.adapters.shared.http"),
    ("frame.adapters.vlm_answers", "match_answers", "frame.adapters.shared.vlm.answers"),
    ("frame.adapters.vlm_answers", "requested_key", "frame.adapters.shared.vlm.answers"),
    ("frame.adapters.video_tools", "require_ffmpeg", "frame.adapters.shared.media.ffmpeg"),
    ("frame.adapters.video_tools", "VideoToolError", "frame.adapters.shared.media.ffmpeg"),
    ("frame.adapters.video_tools", "extract_frame", "frame.adapters.shared.media.frames"),
    ("frame.adapters.video_tools", "Box", "frame.adapters.shared.media.frames"),
    ("frame.adapters.video_tools", "Image", "frame.adapters.shared.media.frames"),
    ("frame.adapters.video_tools", "video_size", "frame.adapters.shared.media.probe"),
    ("frame.adapters.video_tools", "ssim", "frame.adapters.shared.media.similarity"),
    ("frame.adapters.generator.ffmpeg", "resolve_ffmpeg", "frame.adapters.shared.media.ffmpeg"),
    ("frame.adapters.generator.ffmpeg", "probe_video", "frame.adapters.shared.media.probe"),
    ("frame.adapters.generator.ffmpeg", "VideoProbe", "frame.adapters.shared.media.probe"),
    (
        "frame.adapters.generator.ffmpeg",
        "FfmpegVideoNormalizer",
        "frame.adapters.generator.normalizer",
    ),
    ("frame.adapters.generator.ffmpeg", "NormalizedVideo", "frame.adapters.generator.normalizer"),
    ("frame.adapters.generator.ffmpeg", "VideoNormalizer", "frame.adapters.generator.normalizer"),
    ("frame.application.ports.generator", "VideoAssembler", "frame.application.ports.assembler"),
    ("frame.application.ports", "VideoAssembler", "frame.application.ports.assembler"),
    # OpenRouter 모듈은 하위 패키지가 됐다. 예전 모듈 경로가 패키지의 입구다
    # (theme-series 는 _checked_base_url 을 가져다 쓴다)
    (
        "frame.adapters.generator.openrouter",
        "_checked_base_url",
        "frame.adapters.generator.openrouter.generator",
    ),
    (
        "frame.adapters.generator.openrouter",
        "OpenRouterVideoGenerator",
        "frame.adapters.generator.openrouter.generator",
    ),
    (
        "frame.adapters.generator.openrouter",
        "resolve_openrouter_key",
        "frame.adapters.generator.openrouter._api_key",
    ),
    (
        "frame.adapters.generator.openrouter",
        "output_size",
        "frame.adapters.generator.openrouter._output_size",
    ),
    (
        "frame.adapters.generator.openrouter",
        "compile_video_prompt",
        "frame.adapters.generator.openrouter._prompts",
    ),
    (
        "frame.adapters.generator.openrouter",
        "VideoModelInfo",
        "frame.adapters.generator.openrouter._model_catalog",
    ),
]


@pytest.mark.parametrize(("old", "name", "new"), MOVED, ids=[f"{o}.{n}" for o, n, _ in MOVED])
def test_a_name_at_its_old_path_is_the_object_at_its_new_home(old, name, new):
    assert getattr(importlib.import_module(old), name) is getattr(
        importlib.import_module(new), name
    )
