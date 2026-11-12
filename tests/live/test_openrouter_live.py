"""실제 OpenRouter 영상 생성 호출 (slow, 비용 발생). FRAME_GENERATOR=external_api 를 명시했을 때만 실행된다.

    FRAME_GENERATOR=external_api FRAME_VIDEO_KEY_FILE=<OpenRouter 키 파일> FRAME_VIDEO_RESOLUTION=480p \
        python -m pytest tests/live/test_openrouter_live.py -m slow -s

카페 예시의 intro 장면(9:16, 4초) 하나를 만든다. Wan 3.0 기준 480p 약 $0.20, 720p 약 $0.40.
결과 mp4 는 FRAME_ARTIFACT_DIR(기본 ./artifacts) 의 clips/live-<시각>/intro.mp4 에 남는다.
같은 조건으로 24시간 안에 다시 돌리면 작업 원장(ledger/openrouter, ADR-0013)이 이전 결과를 다시 쓰고 실패한다
(과금 없음). 실제 호출을 다시 확인하려면 그 폴더를 지운다.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace

import pytest

from frame.adapters.generator.openrouter import resolve_openrouter_key
from frame.application.ports.generator import SceneGenerationRequest
from frame.config import Settings, build_container
from tests.support.factories import cafe_ir

pytestmark = pytest.mark.slow


@pytest.fixture
def live_settings():
    if os.environ.get("FRAME_GENERATOR") != "external_api":
        pytest.skip(
            "FRAME_GENERATOR=external_api 를 명시하지 않으면 실제 호출을 건너뜀 (비용 발생)"
        )
    settings = Settings.from_env()
    # 재시도·캐시 없이 정확히 한 번만 제출한다
    return replace(
        settings,
        artifact_store="local_fs",
        persistence="memory",
        jobs="recording",
        generator_retries=0,
        generator_cache=False,
    )


def test_openrouter_generates_one_vertical_scene(live_settings):
    container = build_container(live_settings)
    generator = container.generator
    ir = cafe_ir()
    scene = ir.scene("intro")  # 0-4000 ms
    request = SceneGenerationRequest(
        version_id=f"live-{time.strftime('%Y%m%d-%H%M%S')}",
        scene=scene,
        entities=tuple(ir.entity(k) for k in scene.entity_keys),
        seed=20261111,
    )

    started = time.monotonic()
    clip = generator.generate_scene(request)
    elapsed = time.monotonic() - started

    meta = dict(clip.metadata)
    assert meta["ledger"] == "new", (
        "작업 원장이 이전 결과를 다시 썼습니다 (실제 호출 없음). "
        f"{live_settings.artifact_dir}/ledger/openrouter 를 지우고 다시 실행하세요"
    )
    path = container.artifact_store.local_path(clip.uri)
    print(f"\n[live] file = {path}")
    print(f"[live] elapsed = {elapsed:.1f}s (generation {meta['generation_seconds']:.1f}s)")
    printable = {k: v for k, v in meta.items() if k != "prompt"}
    print("[live] metadata =", json.dumps(printable, ensure_ascii=False, indent=1))
    print("[live] prompt =\n" + str(meta["prompt"]))

    assert path is not None and path.suffix == ".mp4" and path.stat().st_size > 0
    assert clip.duration_ms == scene.duration_ms == 4000
    out = meta["output"]
    width, height = generator.output_size
    assert (out["width"], out["height"]) == (width, height)
    assert out["frames"] == round(4000 * generator.fps / 1000)
    assert out["duration_ms"] == 4000
    assert out["has_audio"] is False
    assert meta["billed_seconds"] == 4
    assert meta["job_id"]
    key = resolve_openrouter_key(None, os.environ, live_settings.video_key_file)
    assert key not in json.dumps(meta, ensure_ascii=False)  # 키가 결과에 섞이지 않음
