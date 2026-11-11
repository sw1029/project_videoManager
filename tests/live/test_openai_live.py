"""실제 OpenAI 호출 테스트 (slow). FRAME_STRUCTURER=llm 을 명시했을 때만 실행된다 (비용 발생 방지).

    FRAME_STRUCTURER=llm FRAME_LLM_KEY_FILE=F:/key.txt pytest tests/live -m slow -s

로컬(3090) OpenAI 호환 서버(vLLM 등)는 FRAME_LLM_BASE_URL=http://localhost:8001/v1 과
FRAME_LLM_MODEL=<서빙 모델명>, FRAME_LLM_STRICT_SCHEMA=false 로 같은 테스트를 돌린다.
"""

import os
from dataclasses import replace

import pytest

from frame.adapters.llm.openai_client import resolve_api_key
from frame.application.errors import ConfigurationError, LLMUnavailable
from frame.application.ports.structurer import StructuringRequest
from frame.config import Settings, build_container
from frame.domain.enums import EntityKind

pytestmark = pytest.mark.slow

BRIEF = (
    "카페 신제품 홍보 숏폼 영상. "
    "장면 1 (4초): 바리스타가 카운터에서 빨간 머그컵을 들어 보인다. "
    "장면 2 (4초): 빨간 머그컵 클로즈업, 컵에 'FRAME' 문구가 보인다. "
    "장면 3 (4초): 바리스타가 카메라를 향해 손을 흔든다. "
    "바리스타는 흰 셔츠를 입고 있고, 카페 조명은 따뜻하게 유지한다."
)


@pytest.fixture
def live_settings():
    if os.environ.get("FRAME_STRUCTURER") != "llm":
        pytest.skip("FRAME_STRUCTURER=llm 을 명시하지 않으면 실제 호출을 건너뜀")
    settings = Settings.from_env()
    if settings.llm_provider != "openai":
        pytest.skip("FRAME_LLM_PROVIDER 가 openai 가 아니면 건너뜀")
    return replace(settings, jobs="recording", persistence="memory")


def test_openai_structures_a_korean_brief(live_settings):
    try:
        container = build_container(live_settings)
    except (LLMUnavailable, ConfigurationError) as exc:
        pytest.skip(f"OpenAI 를 쓸 수 없음: {exc}")

    ir = container.structurer.structure(StructuringRequest(BRIEF, target_duration_ms=12000))
    ir.validate()

    print(
        "\n[live] model =", live_settings.llm_model, "| attempts =", container.structurer.attempts
    )
    print("[live] entities:", [(e.entity_key, str(e.kind)) for e in ir.entities])
    for scene in ir.scenes:
        print(
            f"[live] scene {scene.scene_key} {scene.planned_start_ms}-{scene.planned_end_ms}ms "
            f"entities={list(scene.entity_keys)} requirements="
            f"{[(r.entity_key, r.predicate, r.expected_value) for r in scene.requirements]}"
        )

    assert 2 <= len(ir.scenes) <= 4
    assert ir.total_duration_ms == 12000
    assert any(e.kind is EntityKind.PERSON for e in ir.entities)
    assert any(r.predicate == "color" for r in ir.iter_requirements())
    key = resolve_api_key(None, os.environ, live_settings.llm_key_file)
    assert key not in ir.to_json()  # 키가 결과에 섞이지 않음
