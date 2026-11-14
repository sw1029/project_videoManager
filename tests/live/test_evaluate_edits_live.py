"""지표 1 평가 CLI 를 실제 생성·검수 모델로 (slow, 비용 발생). FRAME_EVAL_CASES 로 케이스 파일을 줄 때만 실행된다.

    FRAME_EVAL_CASES=<cases.json> [FRAME_EVAL_OUT=<result.json>] \
    FRAME_PERSISTENCE=sqlalchemy FRAME_DB_URL=<원본이 있는 DB> \
    FRAME_ARTIFACT_STORE=local_fs FRAME_ARTIFACT_DIR=<산출물 폴더> \
    FRAME_GENERATOR=external_api FRAME_ASSEMBLER=ffmpeg \
    FRAME_VIDEO_KEY_FILE=<OpenRouter 키 파일> FRAME_VIDEO_RESOLUTION=480p \
    FRAME_VIDEO_EDIT_MODE=video_reference FRAME_VIDEO_EDIT_MODEL=black-forest-labs/flux-video-edit \
    FRAME_CHECKERS=vlm_requirement,vlm_preservation,ssim_preservation \
    FRAME_LLM_KEY_FILE=<OpenAI 키 파일> FRAME_VLM_MODEL=gpt-4.1 \
        python -m pytest tests/live/test_evaluate_edits_live.py -m slow -s

케이스마다 다시 만드는 장면당 영상 $0.12(편집)~$0.2125(새로 생성)에 검수 VLM 약 $0.05 가 든다 (ADR-0022 의 비용 표).
먼저 같은 설정으로 `python -m frame.tools.evaluate_edits --plan-only` 를 돌려 다시 만들 장면과 편집 여부를 확인한다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from frame.tools import evaluate_edits

pytestmark = pytest.mark.slow


def test_real_models_produce_a_judged_report(tmp_path, capsys):
    cases = os.environ.get("FRAME_EVAL_CASES")
    if not cases:
        pytest.skip("FRAME_EVAL_CASES 로 케이스 파일을 주지 않으면 실제 실행을 건너뜀 (비용 발생)")
    out = Path(os.environ.get("FRAME_EVAL_OUT") or tmp_path / "result.json")
    if out.exists():  # CLI 는 앞 결과(과금된 실행)를 지키려고 돌지 않고 2 로 끝난다
        pytest.fail(f"FRAME_EVAL_OUT 이 이미 있습니다: {out}. 다른 파일을 주세요")

    assert evaluate_edits.main(["--cases", cases, "--out", str(out)]) == 0

    report = json.loads(out.read_text("utf-8"))
    assert report["complete"] is True
    assert set(report["settings"]["checkers"]) >= {"vlm_requirement", "vlm_preservation"}
    for case in report["cases"]:
        assert case["error"] is None, (case["case_id"], case["error"])
        assert case["state"] == "COMPLETED"
        methods = {check["detail"].get("method") for check in case["checks"]}
        assert "vlm" in methods  # 실제 요구 반영 판정
        assert (
            "vlm_keep" in methods or case["by_basis"]["vlm_keep"]["non_target_change_rate"] is None
        )
    print(capsys.readouterr().out)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
