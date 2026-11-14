"""지표 1 결과 파일의 내용 (evaluation/edits 의 _report): 판정 근거는 남기고 이 컴퓨터의 경로는 남기지 않는다.

결과 파일은 저장소에 기록한다 (TDD_WORKFLOW §7). 예외 문구·검수 사유에는 사용자 이름이 든 절대 경로가 섞일 수 있다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from frame.domain.checks import CheckResult, WarningInterval
from frame.domain.enums import CheckType, Verdict, VersionState
from frame.domain.models import VideoVersion
from frame.evaluation.edits import CaseRun, build_report, parse_cases

WINDOWS = "C:\\Users\\kim\\AppData\\Local\\Temp\\frame\\clip 1.mp4"
POSIX = "/home/kim/.cache/frame/parent.mp4"


def _spec():
    (spec,) = parse_cases(
        [
            {
                "case_id": "cup",
                "parent_version_id": "ver_parent",
                "entity_key": "cup",
                "predicate": "color",
                "new_value": "teal",
            }
        ]
    )
    return spec


def _report(run: CaseRun) -> dict:
    return build_report([run], basis="vlm_keep", plan_only=False, meta={}, complete=True)


def test_local_paths_in_error_messages_and_check_reasons_are_masked():
    check = CheckResult(
        "chk_1",
        "ver_r",
        CheckType.PRESERVATION,
        Verdict.UNDETERMINED,
        scene_key="intro",
        warnings=(WarningInterval("intro", 0, 500, f"{POSIX} 의 앞부분을 읽지 못함"),),
        detail={
            "method": "ssim",
            "reason": f"ffmpeg 가 {POSIX} 를 읽지 못했습니다",
            "log": "C:\\Users\\Kim Lee\\AppData\\Local\\ffmpeg.log 를 보세요",
            "frames": [{"source": repr(WINDOWS)}],
            "clip_uri": "local://clips/ver_r/intro.mp4",
            "endpoint": "https://openrouter.ai/api/v1",
            "score_hint": 0.5,
        },
    )
    run = CaseRun(
        _spec(),
        revision_version_id="ver_r",
        error_code="UNEXPECTED_ERROR",
        error_message=f"PermissionError: [Errno 13] Permission denied: {WINDOWS!r}",
        results=(check,),
    )

    report = _report(run)

    text = json.dumps(report, ensure_ascii=False)
    assert "kim" not in text and "Lee" not in text
    case = report["cases"][0]
    assert case["error"]["message"] == "PermissionError: [Errno 13] Permission denied: '<경로>'"
    reported = case["checks"][0]
    assert reported["warnings"][0]["message"] == "<경로> 의 앞부분을 읽지 못함"
    detail = reported["detail"]
    assert detail["reason"] == "ffmpeg 가 <경로> 를 읽지 못했습니다"
    assert detail["log"] == "<경로> 를 보세요"  # 따옴표 없이 공백이 든 사용자 이름까지
    assert detail["frames"] == [{"source": "'<경로>'"}]
    # 저장소 키·웹 주소·숫자는 그대로 둔다
    assert detail["clip_uri"] == "local://clips/ver_r/intro.mp4"
    assert detail["endpoint"] == "https://openrouter.ai/api/v1"
    assert detail["score_hint"] == 0.5


def test_local_path_in_a_failed_revision_message_is_masked():
    failed = VideoVersion(
        "ver_r",
        "prj_1",
        VersionState.FAILED,
        created_at=datetime(2026, 11, 14, tzinfo=UTC),
        error_code="STORAGE_ERROR",
        error_message=f"[WinError 183] 파일이 이미 있습니다: {WINDOWS!r}",
    )
    run = CaseRun(_spec(), revision_version_id="ver_r", version=failed)

    case = _report(run)["cases"][0]

    assert case["error"] == {
        "code": "STORAGE_ERROR",
        "message": "[WinError 183] 파일이 이미 있습니다: '<경로>'",
    }
