"""화면 국소 수정 — 편의 화면 정보·소리가 계획과 어긋난 곳을 찾아 그 부분만 고친다 (ADR-0027).

맡는 개념: 어긋난 곳(ScreenIssue) 하나는 "무엇이, 어느 장면의 언제·어디에서, 어떻게 달랐고, 어떻게 고칠 수 있는가" 다.
종류(IssueKind)마다 가장 좁고 싼 방법(CorrectionMethod)이 정해져 있다. 여기 있는 것은 모두 순수 함수다.
저장·검수 결과 읽기·화면에서 글자 읽기·수정 버전 만들기는 유스케이스(CheckEpisodeScreen · CorrectEpisodeScreen)가 한다.

기존 검수와의 관계: Checker 는 생성한 버전을 보고 CheckResult 를 저장한다 (요구 반영·일관성·보존).
여기서는 그 결과와 편의 계획·녹음을 읽어 '고칠 곳의 목록' 을 그때그때 만든다. ScreenIssue 는 저장하지 않는다.

모듈 지도
- issues   IssueKind · CorrectionMethod · ScreenIssue · 문구를 견주는 규칙 (screen_text_matches)
- finding  어긋난 곳 찾기: 검수 결과에서 (issues_from_checks · without_patched), 계획의 문구에서
           (plan_text_issues · expected_overlay_texts), 녹음에서 (narration_issues)
- fixes    고치는 걸음: fix_overlay_text · patch_scene_text · repair_axis, 결과 기록 CorrectionStep · StepStatus
"""

from frame.domain.series.screen_correction.finding import (
    expected_overlay_texts,
    issues_from_checks,
    narration_issues,
    overlay_text_issue,
    plan_text_issues,
    scene_starts,
    without_patched,
)
from frame.domain.series.screen_correction.fixes import (
    CorrectionStep,
    PatchRegion,
    StepStatus,
    correction_step,
    fix_overlay_text,
    patch_scene_text,
    repair_axis,
)
from frame.domain.series.screen_correction.issues import (
    CorrectionMethod,
    IssueKind,
    ScreenIssue,
    is_text_requirement,
    normalize_screen_text,
    patch_overlay_id,
    screen_text_matches,
)

__all__ = [
    "CorrectionMethod",
    "CorrectionStep",
    "IssueKind",
    "PatchRegion",
    "ScreenIssue",
    "StepStatus",
    "correction_step",
    "expected_overlay_texts",
    "fix_overlay_text",
    "is_text_requirement",
    "issues_from_checks",
    "narration_issues",
    "normalize_screen_text",
    "overlay_text_issue",
    "patch_overlay_id",
    "patch_scene_text",
    "plan_text_issues",
    "repair_axis",
    "scene_starts",
    "screen_text_matches",
    "without_patched",
]
