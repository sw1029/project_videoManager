"""어긋난 곳을 고치는 걸음 (ADR-0027). 순수 함수: 계획 -> 계획. 영상은 다시 만들지 않는다.

- fix_overlay_text : 틀린 문구 항목을 기대값으로 바꾼다 (OVERLAY_EDIT)
- patch_scene_text : 화면 속 틀린 글자를 가리고 다시 쓰는 문구를 더한다 (PATCH)
- repair_axis      : 클립을 다시 만들 때의 축 (객체 · 술어 · 지켜져야 하는 값). 수정 버전은 유스케이스가 만든다
걸음마다 무엇을 했는지 CorrectionStep 으로 남긴다 (고침 · 초안 · 생성 접수 · 다시 녹음 · 건너뜀).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from frame.domain.errors import NotFound, ValidationError
from frame.domain.identity import appearance_attributes
from frame.domain.screen import Region, TextRole
from frame.domain.series.episode import EpisodePlan, OverlayItem, OverlayKind
from frame.domain.series.screen_correction.issues import (
    CorrectionMethod,
    IssueKind,
    ScreenIssue,
    patch_overlay_id,
)
from frame.domain.series.theme import TextStyle


class StepStatus(StrEnum):
    APPLIED = "APPLIED"  # 고쳤다 (완성본을 다시 만들면 반영된다)
    GENERATING = "GENERATING"  # 수정 버전을 만들고 생성을 접수했다
    DRAFTED = "DRAFTED"  # 수정 버전만 만들어 두었다 (생성은 접수하지 않음)
    NEEDS_RERECORD = "NEEDS_RERECORD"  # 그 줄을 다시 녹음해야 한다
    SKIPPED = "SKIPPED"  # 이번에는 고치지 않았다 (까닭은 note)


@dataclass(frozen=True)
class PatchRegion:
    """영상 안의 틀린 글자를 가리고 다시 쓸 자리."""

    issue_id: str
    region: Region
    style: TextStyle | None = None  # 없으면 영역을 덮는 불투명한 띠에 흰 글자


@dataclass(frozen=True)
class CorrectionStep:
    """어긋난 곳 하나에 한 일."""

    issue_id: str
    kind: IssueKind
    method: CorrectionMethod
    status: StepStatus
    scene_key: str
    note: str = ""
    version_id: str | None = None  # 만든 수정 버전
    overlay_id: str | None = None  # 고치거나 더한 문구


def correction_step(
    issue: ScreenIssue,
    status: StepStatus,
    note: str = "",
    *,
    version_id: str | None = None,
    overlay_id: str | None = None,
    method: CorrectionMethod | None = None,
) -> CorrectionStep:
    return CorrectionStep(
        issue_id=issue.issue_id,
        kind=issue.kind,
        method=method or issue.method,
        status=status,
        scene_key=issue.scene_key,
        note=note,
        version_id=version_id,
        overlay_id=overlay_id or issue.overlay_id,
    )


def fix_overlay_text(plan: EpisodePlan, issue: ScreenIssue) -> tuple[EpisodePlan, CorrectionStep]:
    """계획의 문구가 틀린 곳이면 그 문구 항목을 기대값으로 바꾼다.

    화면에서 다르게 읽힌 문구(계획은 맞음)는 글꼴·영역의 문제라 고치지 않고 알린다.
    """
    if issue.detail.get("source") != "plan" or issue.overlay_id is None:
        note = (
            f"얹은 문구가 화면에서 '{issue.observed}' 로 읽힙니다. 글꼴에 그 글자가 있는지, "
            "문구가 영역 안에 들어가는지 확인하세요"
        )
        return plan, correction_step(issue, StepStatus.SKIPPED, note)
    try:
        item = plan.overlay(issue.overlay_id)
    except NotFound:
        return plan, correction_step(issue, StepStatus.SKIPPED, "그 문구 항목이 계획에 없습니다")
    fixed = plan.with_overlay(replace(item, text=issue.expected))
    return fixed, correction_step(issue, StepStatus.APPLIED, f"'{item.text}' -> '{issue.expected}'")


def patch_scene_text(
    plan: EpisodePlan, issue: ScreenIssue, patch: PatchRegion
) -> tuple[EpisodePlan, CorrectionStep]:
    """화면 속 틀린 글자의 영역·시간을 가리고 요구한 글자를 쓰는 문구(PATCH)를 계획에 더한다.

    요구조건 하나에 가린 문구는 하나다 (patch_overlay_id). 다시 고치면 갈아 끼운다.
    """
    scene = plan.ir.scene(issue.scene_key)
    start = max(0, issue.start_ms - scene.planned_start_ms)
    end = min(scene.duration_ms, issue.end_ms - scene.planned_start_ms)
    style = patch.style or TextStyle(
        color="white",
        background="black",
        background_opacity=1.0,
        size=min(0.5, max(0.01, patch.region.h * 0.7)),
    )
    overlay_id = patch_overlay_id(issue.scene_key, issue.entity_key, issue.predicate)
    item = OverlayItem(
        overlay_id=overlay_id,
        scene_key=issue.scene_key,
        text=issue.expected,
        start_ms=start,
        end_ms=end,
        region=patch.region,
        style=style,
        role=TextRole.LABEL,
        kind=OverlayKind.PATCH,
    )
    note = f"'{issue.observed or '틀린 글자'}' 를 가리고 '{issue.expected}' 를 씀"
    step = correction_step(
        issue, StepStatus.APPLIED, note, overlay_id=overlay_id, method=CorrectionMethod.PATCH
    )
    return plan.with_overlay(item), step


def repair_axis(plan: EpisodePlan, issue: ScreenIssue) -> tuple[str | None, str, str] | None:
    """그 어긋남을 다시 맞출 축: (객체, 술어, 지켜져야 하는 값). 없으면 None.

    다시 맞추기(REPAIR)는 요구조건 하나를 축으로 한다 (ADR-0017). 장면마다 다르게 보인 객체(CONSISTENCY)는
    틀린 요구조건이 없으므로, 그 장면에서 그 객체의 모습 조건 하나를 축으로 삼는다.
    """
    if issue.kind is IssueKind.CONSISTENCY:
        if issue.entity_key is None:
            return None
        looks = appearance_attributes(plan.ir.scene(issue.scene_key), issue.entity_key)
        for predicate, value in looks.items():
            return issue.entity_key, predicate, value
        return None
    if issue.predicate is None:
        raise ValidationError(f"요구조건을 알 수 없어 고칠 수 없습니다: {issue.issue_id}")
    return issue.entity_key, issue.predicate, issue.expected
