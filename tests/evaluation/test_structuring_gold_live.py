"""골드셋 15건을 실제 모델로 구조화해 지표를 기록한다 (slow, 비용 발생). FRAME_STRUCTURER=llm 일 때만 돈다.

    FRAME_STRUCTURER=llm FRAME_LLM_KEY_FILE=<키 파일> pytest -m slow tests/evaluation -s

- gpt-4.1-mini 로 건당 $0.001 안팎 (15건, 자기수정 재시도가 있으면 그만큼 더).
- 기록은 docs/evaluation/structuring/ 에 남는다 (FRAME_EVAL_RECORD_DIR 로 바꿀 수 있다).
  그 폴더에서 가장 최근 기록과 견준 표를 함께 출력한다.
- 로컬 OpenAI 호환 서버(vLLM 등)는 FRAME_LLM_BASE_URL·FRAME_LLM_MODEL·FRAME_LLM_STRICT_SCHEMA=false 로
  같은 테스트를 돌린다.
- 목표치(docs/STRUCTURING_DESIGN.md §10)에 못 미치면 기록을 남긴 뒤 실패한다.
"""

import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from frame.adapters.structurer.llm_prompt import PROMPT_VERSION
from frame.application.errors import ConfigurationError, LLMUnavailable
from frame.config import Settings, build_container
from frame.domain.ir import IR_SCHEMA_VERSION
from frame.evaluation.structuring_gold import (
    build_record,
    compare_records,
    format_comparison,
    format_summary,
    latest_record,
    load_gold_cases,
    run_gold_set,
    write_record,
)

pytestmark = pytest.mark.slow

GOLD_DIR = Path(__file__).parent / "structuring_gold"
DEFAULT_RECORD_DIR = Path(__file__).resolve().parents[2] / "docs" / "evaluation" / "structuring"


@pytest.fixture
def live_settings():
    if os.environ.get("FRAME_STRUCTURER") != "llm":
        pytest.skip("FRAME_STRUCTURER=llm 을 명시하지 않으면 실제 호출을 건너뜀 (비용 발생 방지)")
    return replace(Settings.from_env(), jobs="recording", persistence="memory")


def test_gold_set_with_the_configured_model(live_settings):
    try:
        container = build_container(live_settings)
    except (LLMUnavailable, ConfigurationError) as exc:
        pytest.skip(f"LLM 을 쓸 수 없음: {exc}")

    runs = run_gold_set(container.structurer, load_gold_cases(GOLD_DIR))
    record = build_record(
        runs,
        structurer={
            "name": type(container.structurer).__name__,
            "provider": live_settings.llm_provider,
            "model": live_settings.llm_model,
            "base_url": live_settings.llm_base_url,
            "prompt_version": PROMPT_VERSION,
            "schema_version": IR_SCHEMA_VERSION,
        },
        run_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    record_dir = Path(os.environ.get("FRAME_EVAL_RECORD_DIR") or DEFAULT_RECORD_DIR)
    baseline = latest_record(record_dir)
    path = write_record(record, record_dir)

    print(f"\n[gold] 기록: {path}")
    print(format_summary(record))
    if baseline is not None:
        print(format_comparison(compare_records(baseline, record)))
    assert record["summary"]["meets_target"], "목표치에 못 미침 (위 표와 기록 파일 참고)"
