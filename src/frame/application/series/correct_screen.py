"""편의 어긋난 화면 정보를 그 부분만 고친다 (US-01, ADR-0027).

먼저 화면을 검수하고(CheckEpisodeScreen), 고른 어긋남마다 가장 좁은 방법을 쓴다.
- OVERLAY_TEXT (계획의 문구가 틀림): 그 문구 항목을 기대값으로 바꾼다. 영상은 다시 만들지 않는다.
- SCENE_TEXT + 영역: 그 영역·시간을 가리고 다시 쓰는 문구(PATCH)를 더한다. 영상은 다시 만들지 않는다.
- ATTRIBUTE, 영역이 없는 SCENE_TEXT: 그 요구조건이 걸린 장면만 다시 만드는 수정 버전(REPAIR)을 만들고 생성을 접수한다.
  편은 새 버전을 가리키고, 예전 버전은 history 에 남는다. 한 번에 한 가지 조건만 보낸다
  (수정 버전 하나는 한 가지 의도를 갖는다, ADR-0017). 나머지는 그 버전이 끝난 뒤 다시 고친다.
- CONSISTENCY (같은 객체가 장면마다 다르게 보임): 그 장면에서 그 객체의 모습 조건 하나를 축으로 같은 수정 버전(REPAIR)을
  만든다. 그런 조건이 없으면 알리기만 한다.
- NARRATION: 자동으로 고칠 수 없다. 다시 녹음할 줄을 알려 준다 (AttachNarration 에 그 줄만 주면 갈아 끼운다).
- 화면에서 다르게 읽힌 문구(계획은 맞음): 글꼴·영역의 문제라 자동으로 고치지 않고 알린다.

문구를 고친 뒤에는 완성본을 다시 만든다 (RenderEpisode). 고친 장면만 다시 합성된다.

돈이 드는 쪽이 기본이 되지 않게 한다 (감사 BTS-31)
- 고칠 것을 짚지 않으면(issue_ids 가 비어 있으면) 문구 수정만 한다. 클립을 다시 만드는 수정은 SKIPPED 로 알린다.
  클립을 다시 만들려면 그 어긋남을 issue_ids 로 짚는다.
- 수정 버전은 초안까지만 만든다. 생성을 바로 접수하려면 generate=True 를 준다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from frame.domain.correction import CorrectionMethod, IssueKind, ScreenIssue, patch_overlay_id
from frame.domain.enums import ChangeScope, RevisionKind
from frame.domain.episode import EpisodePlan, OverlayItem, OverlayKind
from frame.domain.errors import NotFound, ValidationError
from frame.domain.revision import KeepItem
from frame.domain.screen import Region, TextRole
from frame.domain.series import Episode
from frame.domain.services.identity import appearance_attributes
from frame.domain.services.propagation import AttributeChange
from frame.domain.theme import TextStyle

from ..dto import CreateRevisionCommand
from ..ports.theme_store import EpisodeRepository
from ..use_cases.accept_generation import AcceptGeneration
from ..use_cases.create_revision import CreateRevision
from .screen_check import CheckEpisodeScreen, ScreenReport


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
class CorrectScreenCommand:
    episode_id: str
    # 비어 있으면 찾은 것 가운데 영상을 다시 만들지 않는 것만 고친다. 클립 수정은 여기서 짚은 것만 한다
    issue_ids: tuple[str, ...] = ()
    patches: tuple[PatchRegion, ...] = ()
    keep: tuple[KeepItem, ...] = ()  # 클립을 다시 만들 때 그대로 둘 요소
    generate: bool = False  # 수정 버전의 생성을 바로 접수한다 (과금). 기본은 초안까지


@dataclass(frozen=True)
class CorrectionStep:
    issue_id: str
    kind: IssueKind
    method: CorrectionMethod
    status: StepStatus
    scene_key: str
    note: str = ""
    version_id: str | None = None  # 만든 수정 버전
    overlay_id: str | None = None  # 고치거나 더한 문구


@dataclass(frozen=True)
class CorrectScreenResult:
    episode: Episode
    steps: tuple[CorrectionStep, ...]
    report: ScreenReport  # 고치기 전의 검수 결과

    @property
    def pending_version_id(self) -> str | None:
        """생성이 끝나기를 기다려야 하는 수정 버전."""
        return next((s.version_id for s in self.steps if s.version_id is not None), None)

    @property
    def recomposed_scene_keys(self) -> tuple[str, ...]:
        """문구를 고쳐 다시 합성될 장면."""
        scenes = [s.scene_key for s in self.steps if s.status is StepStatus.APPLIED]
        return tuple(dict.fromkeys(scenes))


class CorrectEpisodeScreen:
    def __init__(
        self,
        episodes: EpisodeRepository,
        check: CheckEpisodeScreen,
        create_revision: CreateRevision,
        accept_generation: AcceptGeneration | None = None,
    ) -> None:
        self._episodes = episodes
        self._check = check
        self._create_revision = create_revision
        self._accept_generation = accept_generation

    def execute(self, command: CorrectScreenCommand) -> CorrectScreenResult:
        report = self._check.execute(command.episode_id)
        episode = self._episodes.get(command.episode_id)
        assert episode is not None  # 검수가 이미 찾았다
        issues = _selected(report, command.issue_ids)
        patches = {p.issue_id: p for p in command.patches}
        unknown = sorted(set(patches) - {i.issue_id for i in issues})
        if unknown:
            raise ValidationError(f"가릴 자리를 준 어긋남이 고칠 대상에 없습니다: {unknown}")

        plan = episode.plan
        named = set(command.issue_ids)
        steps: list[CorrectionStep] = []
        clip_edits: list[ScreenIssue] = []
        for issue in issues:
            if issue.kind is IssueKind.NARRATION:
                steps.append(
                    _step(issue, StepStatus.NEEDS_RERECORD, f"다시 녹음할 줄: {issue.expected}")
                )
            elif issue.kind is IssueKind.OVERLAY_TEXT:
                plan, step = _fix_overlay(plan, issue)
                steps.append(step)
            elif issue.issue_id in patches:
                if issue.kind is not IssueKind.SCENE_TEXT:
                    raise ValidationError(
                        f"가리고 다시 쓰는 수정은 화면 속 글자에만 쓸 수 있습니다: {issue.issue_id}"
                    )
                plan, step = _patch(plan, issue, patches[issue.issue_id])
                steps.append(step)
            elif issue.issue_id in named:
                clip_edits.append(issue)
            else:
                steps.append(
                    _step(
                        issue,
                        StepStatus.SKIPPED,
                        "클립을 다시 만드는 수정(과금)은 어긋남을 issue_ids 로 짚었을 때만 합니다",
                    )
                )

        if plan is not episode.plan:
            episode = replace(episode, plan=plan, render=None)  # 완성본은 다시 만들어야 한다
        if clip_edits:
            episode, edit_steps = self._edit_clips(episode, clip_edits, command)
            steps += edit_steps
        self._episodes.save(episode)
        return CorrectScreenResult(episode, tuple(steps), report)

    def _edit_clips(
        self, episode: Episode, issues: Sequence[ScreenIssue], command: CorrectScreenCommand
    ) -> tuple[Episode, list[CorrectionStep]]:
        """같은 조건(객체·술어·값)이 걸린 장면들을 한 수정 버전으로 다시 만든다. 다른 조건은 다음으로 미룬다.

        다시 맞추기(REPAIR)는 요구조건 하나를 축으로 한다 (ADR-0017). 장면마다 다르게 보인 객체(CONSISTENCY)는
        틀린 요구조건이 없으므로, 그 장면에서 그 객체의 모습 조건 하나를 축으로 삼는다. 없으면 알리기만 한다.
        """
        axes: dict[str, tuple[str | None, str, str]] = {}
        skipped: list[CorrectionStep] = []
        for issue in issues:
            axis = _axis(episode.plan, issue)
            if axis is None:
                skipped.append(
                    _step(
                        issue,
                        StepStatus.SKIPPED,
                        f"장면 '{issue.scene_key}' 에 '{issue.entity_key}' 의 모습 조건이 없어 "
                        "다시 맞출 축이 없습니다. 조건을 더한 뒤 다시 만드세요",
                    )
                )
            else:
                axes[issue.issue_id] = axis
        issues = [i for i in issues if i.issue_id in axes]
        if not issues:
            return episode, skipped
        first = issues[0]
        target = axes[first.issue_id]
        group = [i for i in issues if axes[i.issue_id] == target]
        rest = [i for i in issues if i not in group]
        change = AttributeChange(
            entity_key=target[0],
            predicate=target[1],
            new_value=target[2],
            scope=ChangeScope.SCENES,
            scene_keys=tuple(dict.fromkeys(i.scene_key for i in group)),
        )
        created = self._create_revision.execute(
            CreateRevisionCommand(
                version_id=episode.version_id,
                change=change,
                kind=RevisionKind.REPAIR,
                keep=command.keep,
                request_text="화면 정보 국소 수정: " + ", ".join(i.issue_id for i in group),
            )
        )
        status = StepStatus.DRAFTED
        if command.generate and self._accept_generation is not None:
            self._accept_generation.execute(created.version_id)
            status = StepStatus.GENERATING
        steps = [
            _step(
                issue,
                status,
                f"장면 '{issue.scene_key}' 만 다시 만드는 수정 버전",
                version_id=created.version_id,
            )
            for issue in group
        ]
        steps += [
            _step(
                issue,
                StepStatus.SKIPPED,
                "수정 버전 하나는 한 가지 조건만 고칩니다. 이 버전이 끝난 뒤 다시 고치세요",
            )
            for issue in rest
        ]
        return episode.with_version(created.version_id), [*steps, *skipped]


def _axis(plan: EpisodePlan, issue: ScreenIssue) -> tuple[str | None, str, str] | None:
    """그 어긋남을 다시 맞출 축: (객체, 술어, 지켜져야 하는 값). 없으면 None."""
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


def _selected(report: ScreenReport, issue_ids: Sequence[str]) -> list[ScreenIssue]:
    if not issue_ids:
        return list(report.issues)
    return [report.issue(issue_id) for issue_id in dict.fromkeys(issue_ids)]


def _step(
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


def _fix_overlay(plan: EpisodePlan, issue: ScreenIssue) -> tuple[EpisodePlan, CorrectionStep]:
    if issue.detail.get("source") != "plan" or issue.overlay_id is None:
        note = (
            f"얹은 문구가 화면에서 '{issue.observed}' 로 읽힙니다. 글꼴에 그 글자가 있는지, "
            "문구가 영역 안에 들어가는지 확인하세요"
        )
        return plan, _step(issue, StepStatus.SKIPPED, note)
    try:
        item = plan.overlay(issue.overlay_id)
    except NotFound:
        return plan, _step(issue, StepStatus.SKIPPED, "그 문구 항목이 계획에 없습니다")
    fixed = plan.with_overlay(replace(item, text=issue.expected))
    return fixed, _step(issue, StepStatus.APPLIED, f"'{item.text}' -> '{issue.expected}'")


def _patch(
    plan: EpisodePlan, issue: ScreenIssue, patch: PatchRegion
) -> tuple[EpisodePlan, CorrectionStep]:
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
    step = _step(
        issue, StepStatus.APPLIED, note, overlay_id=overlay_id, method=CorrectionMethod.PATCH
    )
    return plan.with_overlay(item), step
