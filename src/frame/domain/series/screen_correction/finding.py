"""어긋난 곳 찾기 (ADR-0027). 순수 함수: 검수 결과·편의 계획·녹음 기록에서 ScreenIssue 를 만든다.

- issues_from_checks : 요구 반영·일관성 검수의 FAIL -> SCENE_TEXT · ATTRIBUTE · CONSISTENCY
- without_patched    : 가리고 다시 써서 이미 고친 화면 속 글자를 뺀다 (빼지 않으면 고친 것을 또 고치라고 한다)
- plan_text_issues   : 계획의 화면 문구가 테마의 문구 틀과 다른 곳 -> OVERLAY_TEXT (영상을 보지 않고 잡는다)
- overlay_text_issue : 얹은 문구 하나의 어긋남. 완성본에서 읽은 글자가 다를 때 유스케이스가 쓴다
- narration_issues   : 대본과 다르게 읽은 줄, 읽지 않은 줄, 장면보다 긴 줄 -> NARRATION

화면에서 글자를 읽는 일(유료 호출)은 여기 없다. 그것은 유스케이스 CheckEpisodeScreen 이 포트로 한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from frame.domain.checks import CheckResult
from frame.domain.enums import CheckType, Verdict
from frame.domain.errors import ValidationError
from frame.domain.ir import IntermediateRepresentation
from frame.domain.series.episode import EpisodePlan, OverlayItem, OverlayKind
from frame.domain.series.narration import LineStatus, PlacementStatus
from frame.domain.series.record import Episode
from frame.domain.series.screen_correction.issues import (
    CorrectionMethod,
    IssueKind,
    ScreenIssue,
    is_text_requirement,
    patch_overlay_id,
    screen_text_matches,
)
from frame.domain.series.theme import Theme, render_template


# ---- 1. 영상의 요구조건·일관성 (검수 결과) ----
def issues_from_checks(
    ir: IntermediateRepresentation, results: Sequence[CheckResult]
) -> tuple[ScreenIssue, ...]:
    """검수의 FAIL 을 어긋난 곳으로 옮긴다 (장면 순서. 장면 안에서는 요구조건 순서, 그 뒤에 일관성).

    - 요구 반영 검수(REQUIREMENT): 구간은 검수가 남긴 경고 구간을 쓰고, 없으면 그 장면 전체다.
      문구·브랜드 요구조건은 SCENE_TEXT, 그 밖에는 ATTRIBUTE 다.
    - 일관성 검수(CONSISTENCY): 같은 객체가 기준과 다르게 보인 장면마다 하나. 경고 구간이 없으면 그 객체가 나오는
      모든 장면이다. 요구조건은 모두 맞아도 같은 물건이 장면마다 다르면 화면이 틀린 것이다 (둘째 실호출).
    통과·미판정은 어긋난 곳이 아니다.
    """
    failed = {
        r.requirement_key: r
        for r in results
        if r.check_type is CheckType.REQUIREMENT
        and r.verdict is Verdict.FAIL
        and r.requirement_key is not None
    }
    inconsistent = [
        r
        for r in results
        if r.check_type is CheckType.CONSISTENCY
        and r.verdict is Verdict.FAIL
        and r.entity_key is not None
        and ir.has_entity(r.entity_key)
    ]
    issues: list[ScreenIssue] = []
    for scene in ir.scenes:
        for requirement in scene.requirements:
            result = failed.get(requirement.key)
            if result is None:
                continue
            spans = [
                (w.start_ms, w.end_ms) for w in result.warnings if w.scene_key == scene.scene_key
            ]
            start = min((s for s, _ in spans), default=scene.planned_start_ms)
            end = max((e for _, e in spans), default=scene.planned_end_ms)
            text = is_text_requirement(requirement)
            issues.append(
                ScreenIssue(
                    issue_id=f"requirement:{requirement.key}",
                    kind=IssueKind.SCENE_TEXT if text else IssueKind.ATTRIBUTE,
                    scene_key=scene.scene_key,
                    start_ms=start,
                    end_ms=end,
                    expected=requirement.expected_value,
                    observed=str(result.detail.get("observed") or ""),
                    # 영역을 알기 전에는 글자도 클립 편집으로 고친다. 영역이 주어지면 PATCH 로 바꾼다
                    method=CorrectionMethod.CLIP_EDIT,
                    requirement_key=requirement.key,
                    entity_key=requirement.entity_key,
                    predicate=requirement.predicate,
                    detail={"check_id": result.check_id, "score": result.score},
                )
            )
        for result in inconsistent:
            if result.entity_key is None or result.entity_key not in scene.entity_keys:
                continue
            here = [w for w in result.warnings if w.scene_key == scene.scene_key]
            if result.warnings and not here:
                continue  # 경고가 다른 장면을 짚었다
            issues.append(
                ScreenIssue(
                    issue_id=f"consistency:{scene.scene_key}:{result.entity_key}",
                    kind=IssueKind.CONSISTENCY,
                    scene_key=scene.scene_key,
                    start_ms=min((w.start_ms for w in here), default=scene.planned_start_ms),
                    end_ms=max((w.end_ms for w in here), default=scene.planned_end_ms),
                    expected="",  # 기대는 글이 아니라 기준 이미지다
                    observed="; ".join(dict.fromkeys(w.message for w in here)),
                    method=CorrectionMethod.CLIP_EDIT,
                    entity_key=result.entity_key,
                    detail={
                        "check_id": result.check_id,
                        "score": result.score,
                        "reference": result.detail.get("reference"),
                    },
                )
            )
    return tuple(issues)


def without_patched(
    plan: EpisodePlan, found: Sequence[ScreenIssue]
) -> tuple[list[ScreenIssue], list[str]]:
    """요구한 글자로 가리고 다시 쓴 화면 속 글자를 뺀다. (남은 어긋남, 뺀 것의 id)

    클립은 그대로이므로 요구 반영 검수는 여전히 FAIL 이다. 요구한 글자로 가린 문구(PATCH)가 계획에 있으면
    그 요구조건은 고친 것이다. 가린 문구가 제대로 보이는지는 화면에서 읽는 검사가 다른 문구와 같이 확인한다.
    """
    patches = {item.overlay_id: item for item in plan.overlays if item.kind is OverlayKind.PATCH}
    issues: list[ScreenIssue] = []
    patched: list[str] = []
    for issue in found:
        item = patches.get(patch_overlay_id(issue.scene_key, issue.entity_key, issue.predicate))
        settled = (
            issue.kind is IssueKind.SCENE_TEXT
            and item is not None
            and screen_text_matches(issue.expected, item.text)
        )
        if settled:
            patched.append(issue.issue_id)
        else:
            issues.append(issue)
    return issues, patched


# ---- 2. 화면 문구 (계획) ----
def expected_overlay_texts(theme: Theme, variables: Mapping[str, str]) -> dict[str, str]:
    """문구 항목 id -> 테마의 문구 틀에 편의 변수 값을 넣은 문구.

    자리마다 값이 다른 틀(자리별 자막)은 기대값이 없다: 그 자리의 값이 곧 내용이다.
    """
    expected: dict[str, str] = {}
    for rule in theme.text_rules:
        try:
            text = render_template(rule.template, variables)
        except ValidationError:
            continue  # 편 단위 값으로 채워지지 않는 틀
        for slot in theme.format.slots:
            if not rule.slot_keys or slot.slot_key in rule.slot_keys:
                expected[f"{slot.slot_key}:{rule.rule_key}"] = text
    return expected


def scene_starts(plan: EpisodePlan) -> dict[str, int]:
    """장면 키 -> 편의 타임라인에서 그 장면이 시작하는 시각."""
    return {scene.scene_key: scene.planned_start_ms for scene in plan.ir.scenes}


def overlay_text_issue(
    item: OverlayItem,
    scene_start_ms: int,
    *,
    expected: str,
    observed: str,
    detail: Mapping[str, Any],
) -> ScreenIssue:
    """얹은 문구 하나가 어긋난 곳. 문구 항목의 시각(장면 안)을 편의 타임라인으로 옮긴다."""
    return ScreenIssue(
        issue_id=f"overlay:{item.overlay_id}",
        kind=IssueKind.OVERLAY_TEXT,
        scene_key=item.scene_key,
        start_ms=scene_start_ms + item.start_ms,
        end_ms=scene_start_ms + item.end_ms,
        expected=expected,
        observed=observed,
        method=CorrectionMethod.OVERLAY_EDIT,
        region=item.region,
        overlay_id=item.overlay_id,
        detail=detail,
    )


def plan_text_issues(theme: Theme, plan: EpisodePlan) -> list[ScreenIssue]:
    """계획의 문구 항목이 테마의 문구 틀에 편의 변수 값을 넣은 것과 다른 곳.

    기획이나 사람이 문구를 잘못 넣은 경우다 (가격을 틀리게 적음). 영상을 보지 않고 잡는다.
    """
    expected = expected_overlay_texts(theme, plan.variables)
    starts = scene_starts(plan)
    issues: list[ScreenIssue] = []
    for item in plan.overlays:
        want = expected.get(item.overlay_id)
        if item.kind is not OverlayKind.TEXT or want is None:
            continue
        if screen_text_matches(want, item.text):
            continue
        issues.append(
            overlay_text_issue(
                item,
                starts[item.scene_key],
                expected=want,
                observed=item.text,
                detail={"source": "plan", "rule_key": item.rule_key},
            )
        )
    return issues


# ---- 3. 녹음 ----
def narration_issues(episode: Episode) -> list[ScreenIssue]:
    """대본과 다르게 읽은 줄, 읽지 않은 줄, 장면보다 긴 줄. 녹음을 붙이지 않은 편은 빈 목록이다."""
    narration = episode.narration
    if narration is None:
        return []
    placements = {p.line_key: p for p in narration.placements}
    issues: list[ScreenIssue] = []
    for alignment in narration.alignments:
        line = episode.plan.line(alignment.line_key)
        placement = placements.get(alignment.line_key)
        overflow = placement is not None and placement.status is PlacementStatus.OVERFLOW
        if alignment.status is LineStatus.MATCHED and not overflow:
            continue
        scene = episode.plan.ir.scene(line.scene_key)
        reason = (
            "overflow" if alignment.status is LineStatus.MATCHED else str(alignment.status).lower()
        )
        issues.append(
            ScreenIssue(
                issue_id=f"narration:{line.line_key}",
                kind=IssueKind.NARRATION,
                scene_key=line.scene_key,
                start_ms=scene.planned_start_ms,
                end_ms=scene.planned_end_ms,
                expected=line.text,
                observed=alignment.heard,
                method=CorrectionMethod.RERECORD,
                line_key=line.line_key,
                detail={
                    "reason": reason,
                    "similarity": round(alignment.similarity, 4),
                    "missing_terms": list(alignment.missing_terms),
                    "overflow_ms": placement.overflow_ms if placement is not None else 0,
                },
            )
        )
    return issues
