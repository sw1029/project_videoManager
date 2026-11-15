"""시연 프리셋(frontend/public/presets.json)이 실제 구조화 규칙을 통과하는지 확인한다.

DSL 프리셋은 규칙 기반 구조화기로 바로 검증하고, 자연어 프리셋은 필드·길이만 확인한다 (LLM 호출 없음).
"""

import json
from pathlib import Path

import pytest

from frame.adapters.structurer import RuleBasedStructurer
from frame.application.ports.structurer import StructuringRequest
from frame.presentation.schemas import MAX_PROMPT_CHARS, MAX_TARGET_DURATION_MS

PRESETS_FILE = Path(__file__).resolve().parents[2] / "frontend" / "public" / "presets.json"
pytestmark = pytest.mark.skipif(not PRESETS_FILE.is_file(), reason="frontend/ 가 없는 브랜치")


def _presets() -> list[dict]:
    if not PRESETS_FILE.is_file():
        return []
    return json.loads(PRESETS_FILE.read_text(encoding="utf-8"))["presets"]


def test_presets_are_well_formed():
    presets = _presets()
    assert len({p["id"] for p in presets}) == len(presets)
    assert {p["mode"] for p in presets} == {"natural_language", "dsl"}
    for p in presets:
        assert p["title"].strip() and p["prompt"].strip()
        assert len(p["prompt"]) <= MAX_PROMPT_CHARS
        assert 0 < p["duration_s"] * 1000 <= MAX_TARGET_DURATION_MS


@pytest.mark.parametrize(
    "preset", [p for p in _presets() if p["mode"] == "dsl"], ids=lambda p: p["id"]
)
def test_dsl_presets_structure_cleanly_with_matching_duration(preset):
    target_ms = preset["duration_s"] * 1000
    ir = RuleBasedStructurer().structure(
        StructuringRequest(preset["prompt"], target_duration_ms=target_ms)
    )
    ir.validate()
    assert ir.total_duration_ms == target_ms
