"""골드셋 15건을 실제 모델로 구조화해 지표를 기록한다 (slow, 비용 발생). 이 테스트만의 스위치로 켠다.

    FRAME_EVAL_STRUCTURING_LIVE=1 FRAME_LLM_KEY_FILE=<키 파일> \
        pytest -m slow tests/live/test_structuring_gold_live.py -s

- 켜는 것은 FRAME_EVAL_STRUCTURING_LIVE=1 하나다. 서버 설정(FRAME_STRUCTURER)을 보지 않고 LLM 구조화기로 돈다.
  켰는데 키·패키지가 없으면 건너뛰지 않고 실패한다 (설정 오류).
- 비용: gpt-4.1-mini 로 1회 약 $0.03 (15건, 자기수정 재시도 포함, 2026-11-14 실측).
- 기록은 기본으로 임시 폴더에 쓴다. 저장소에 남길 때만 FRAME_EVAL_RECORD_DIR=docs/evaluation/structuring 을 준다.
  비교 기준은 그 폴더(주지 않으면 docs/evaluation/structuring)의 가장 최근 기록이다.
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

SWITCH = "FRAME_EVAL_STRUCTURING_LIVE"
ROOT = Path(__file__).resolve().parents[2]
GOLD_DIR = ROOT / "tests" / "evaluation" / "structuring_gold"
COMMITTED_RECORDS = ROOT / "docs" / "evaluation" / "structuring"


@pytest.fixture
def live_settings():
    if os.environ.get(SWITCH) != "1":
        pytest.skip(f"{SWITCH}=1 일 때만 실제 모델을 부른다 (비용 발생 방지)")
    return replace(Settings.from_env(), structurer="llm", jobs="recording", persistence="memory")


def test_gold_set_with_the_configured_model(live_settings, tmp_path):
    try:
        container = build_container(live_settings)
    except (LLMUnavailable, ConfigurationError) as exc:
        pytest.fail(f"{SWITCH}=1 인데 LLM 을 쓸 수 없다 (설정 오류): {exc}")

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
    target = os.environ.get("FRAME_EVAL_RECORD_DIR")
    baseline = latest_record(Path(target) if target else COMMITTED_RECORDS)
    path = write_record(record, Path(target) if target else tmp_path)

    print(f"\n[gold] 기록: {path}")
    print(format_summary(record))
    if baseline is not None:
        print(format_comparison(compare_records(baseline, record)))
    assert record["summary"]["meets_target"], "목표치에 못 미침 (위 표와 기록 파일 참고)"
