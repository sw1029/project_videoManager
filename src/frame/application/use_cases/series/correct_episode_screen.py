"""편의 어긋난 화면 정보를 그 부분만 고친다 (US-01, ADR-0027).

먼저 화면을 검수하고(CheckEpisodeScreen), 고른 어긋남마다 가장 좁은 방법을 쓴다.
- OVERLAY_TEXT (계획의 문구가 틀림): 그 문구 항목을 기대값으로 바꾼다. 영상은 다시 만들지 않는다.
- SCENE_TEXT + 영역: 그 영역·시간을 가리고 다시 쓰는 문구(PATCH)를 더한다. 영상은 다시 만들지 않는다.
- ATTRIBUTE, 영역이 없는 SCENE_TEXT: 그 요구조건이 걸린 장면만 다시 만드는 수정 버전(REPAIR)을 만들고 생성을 접수한다.
  편은 그 버전을 '만들고 있는 버전' 으로 적어 두고, 그 버전이 끝난 뒤에 기준 버전을 옮긴다 (실패하면 잊는다).
  예전 버전은 history 에 남는다. 만들고 있는 버전이 있는 동안에는 클립 수정을 더 받지 않는다.
  한 번에 한 가지 조건만 보낸다
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

from frame.application.dto import CreateRevisionCommand
from frame.application.ports.series.repositories import EpisodeRepository
from frame.application.ports.unit_of_work import UnitOfWorkFactory
from frame.application.use_cases.accept_generation import AcceptGeneration
from frame.application.use_cases.create_revision import CreateRevision
from frame.application.use_cases.series._pending_revision import require_episode, settle_pending
from frame.application.use_cases.series.check_episode_screen import CheckEpisodeScreen, ScreenReport
from frame.domain.enums import ChangeScope, RevisionKind
from frame.domain.errors import ValidationError
from frame.domain.revision import AttributeChange, KeepItem
from frame.domain.series.record import Episode
from frame.domain.series.screen_correction import (
    CorrectionStep,
    IssueKind,
    PatchRegion,
    ScreenIssue,
    StepStatus,
    correction_step,
    fix_overlay_text,
    patch_scene_text,
    repair_axis,
    without_patched,
)


@dataclass(frozen=True)
class CorrectScreenCommand:
    episode_id: str
    # 비어 있으면 찾은 것 가운데 영상을 다시 만들지 않는 것만 고친다. 클립 수정은 여기서 짚은 것만 한다
    issue_ids: tuple[str, ...] = ()
    patches: tuple[PatchRegion, ...] = ()
    keep: tuple[KeepItem, ...] = ()  # 클립을 다시 만들 때 그대로 둘 요소
    generate: bool = False  # 수정 버전의 생성을 바로 접수한다 (과금). 기본은 초안까지
    # 방금 받은 검수 보고서. 주면 검수를 다시 돌리지 않는다 (화면 문구 읽기는 문구마다 유료 호출 한 번이다).
    # 가리기는 버전을 바꾸지 않으므로 가리기 전의 보고서도 받는다. 그 보고서의 이미 가린 글자는 다시 만들지 않는다
    report: ScreenReport | None = None


@dataclass(frozen=True)
class CorrectScreenResult:
    episode: Episode
    steps: tuple[CorrectionStep, ...]
    report: ScreenReport  # 고치기 전의 검수 결과 (문구를 고치거나 가린 뒤에는 낡은 것이다)

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
        uow_factory: UnitOfWorkFactory,
        create_revision: CreateRevision,
        accept_generation: AcceptGeneration | None = None,
    ) -> None:
        self._episodes = episodes
        self._check = check
        self._uow_factory = uow_factory
        self._create_revision = create_revision
        self._accept_generation = accept_generation

    def execute(self, command: CorrectScreenCommand) -> CorrectScreenResult:
        with self._episodes.lock(command.episode_id):  # 읽고 -> 고쳐 -> 덮어쓰는 사이를 지킨다
            return self._correct(command)

    def _correct(self, command: CorrectScreenCommand) -> CorrectScreenResult:
        episode = settle_pending(
            self._episodes, self._uow_factory, require_episode(self._episodes, command.episode_id)
        )
        report = command.report or self._check.execute(command.episode_id)
        if report.episode_id != episode.episode_id:
            raise ValidationError("다른 편의 검수 보고서입니다")
        if report.version_id != episode.version_id:
            raise ValidationError("검수 보고서가 편의 지금 버전의 것이 아닙니다. 다시 검수하세요")
        issues = _selected(report, command.issue_ids)
        patches = {p.issue_id: p for p in command.patches}
        unknown = sorted(set(patches) - {i.issue_id for i in issues})
        if unknown:
            raise ValidationError(f"가릴 자리를 준 어긋남이 고칠 대상에 없습니다: {unknown}")
        misplaced = sorted(
            i.issue_id
            for i in issues
            if i.issue_id in patches and i.kind is not IssueKind.SCENE_TEXT
        )
        if misplaced:  # 얹은 문구·녹음에 준 가리기는 앞 분기에서 걸러져 말없이 무시됐다
            raise ValidationError(
                f"가리고 다시 쓰는 수정은 화면 속 글자에만 쓸 수 있습니다: {misplaced}"
            )

        plan = episode.plan
        named = set(command.issue_ids)
        # 받은 보고서가 가리기 전의 것이면 가린 글자가 아직 어긋난 곳으로 들어 있다 (감사 BTS-D02)
        already_patched = set(without_patched(plan, issues)[1])
        steps: list[CorrectionStep] = []
        clip_edits: list[ScreenIssue] = []
        for issue in issues:
            if issue.kind is IssueKind.NARRATION:
                steps.append(
                    correction_step(
                        issue, StepStatus.NEEDS_RERECORD, f"다시 녹음할 줄: {issue.expected}"
                    )
                )
            elif issue.kind is IssueKind.OVERLAY_TEXT:
                plan, step = fix_overlay_text(plan, issue)
                steps.append(step)
            elif issue.issue_id in patches:
                plan, step = patch_scene_text(plan, issue, patches[issue.issue_id])
                steps.append(step)
            elif issue.issue_id in already_patched:
                steps.append(
                    correction_step(
                        issue,
                        StepStatus.SKIPPED,
                        "이미 가리고 다시 쓴 글자입니다. 클립을 다시 만들지 않습니다 "
                        "(가릴 자리를 바꾸려면 patches 로 다시 주세요)",
                    )
                )
            elif issue.issue_id in named:
                clip_edits.append(issue)
            else:
                steps.append(
                    correction_step(
                        issue,
                        StepStatus.SKIPPED,
                        "클립을 다시 만드는 수정(과금)은 어긋남을 issue_ids 로 짚었을 때만 합니다",
                    )
                )

        if plan is not episode.plan:
            # 문구 수정을 먼저 저장한다. 뒤의 수정 버전 만들기·생성 접수가 실패해도 문구 수정은 남는다
            episode = replace(episode, plan=plan, render=None)  # 완성본은 다시 만들어야 한다
            self._episodes.save(episode)
        if clip_edits:
            episode, edit_steps = self._edit_clips(episode, clip_edits, command)
            steps += edit_steps
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
            axis = repair_axis(episode.plan, issue)
            if axis is None:
                skipped.append(
                    correction_step(
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
        if episode.pending_version_id is not None:
            waiting = (
                f"이 편은 수정 버전 {episode.pending_version_id} 을 만들고 있습니다. "
                "그 버전이 끝난 뒤(또는 초안을 버린 뒤) 다시 고치세요"
            )
            return episode, [
                *(correction_step(i, StepStatus.SKIPPED, waiting) for i in issues),
                *skipped,
            ]
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
        # 편의 기준 버전은 옮기지 않는다. 만들고 있는 버전으로만 적어 두고, 끝난 뒤에 옮긴다 (감사 BTS-16).
        # 생성 접수보다 먼저 저장한다: 접수가 실패해도 초안이 편에 적혀 있다
        episode = episode.with_pending(created.version_id)
        self._episodes.save(episode)
        status = StepStatus.DRAFTED
        if command.generate and self._accept_generation is not None:
            self._accept_generation.execute(created.version_id)
            status = StepStatus.GENERATING
            # 작업을 그 자리에서 끝까지 돌리는 구성(inline)이면 벌써 끝나 있다
            episode = settle_pending(self._episodes, self._uow_factory, episode)
        steps = [
            correction_step(
                issue,
                status,
                f"장면 '{issue.scene_key}' 만 다시 만드는 수정 버전",
                version_id=created.version_id,
            )
            for issue in group
        ]
        steps += [
            correction_step(
                issue,
                StepStatus.SKIPPED,
                "수정 버전 하나는 한 가지 조건만 고칩니다. 이 버전이 끝난 뒤 다시 고치세요",
            )
            for issue in rest
        ]
        return episode, [*steps, *skipped]


def _selected(report: ScreenReport, issue_ids: Sequence[str]) -> list[ScreenIssue]:
    if not issue_ids:
        return list(report.issues)
    return [report.issue(issue_id) for issue_id in dict.fromkeys(issue_ids)]
