"""편의 녹음 대본만 고친다 (ADR-0029). 영상은 그대로 둔다.

바뀌는 것
- 대본 줄의 글과 어림 길이, 그 줄을 따르는 자막 (말을 받아 적는 자막 규칙). 규칙은 domain/series/script_revision.
- 녹음을 붙인 편이면: 바뀐 줄의 녹음은 이제 대본과 다르므로 트랙에서 빼고 (그 자리는 무음), 다시 녹음할 줄로 알린다.
  나머지 줄의 녹음은 그대로 쓴다. 인식을 다시 하지 않는다.
바뀌지 않는 것
- 편의 기준 버전, IR, 클립. 영상을 다시 만들지 않는다 (생성 호출 없음).
- 편의 변수 값과 변수로 만든 화면 문구. 고친 줄이 그 값과 어긋나면 warnings 로 알린다.

다시 녹음한 줄은 AttachNarration 에 그 줄만 주면 갈아 끼우고 대본과 맞는지 검사한다 (줄 단위 경로).
자막이나 소리가 바뀌면 완성본은 다시 만들어야 한다 (RenderEpisode. 바뀐 장면만 다시 합성된다).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.series.audio import AudioEditor
from frame.application.ports.series.repositories import EpisodeRepository, ThemeRepository
from frame.application.ports.unit_of_work import UnitOfWorkFactory
from frame.application.use_cases.series._narration_track import narration_track
from frame.application.use_cases.series._pending_revision import require_episode, settle_pending
from frame.domain.series.narration import (
    DEFAULT_NARRATION_POLICY,
    LineAlignment,
    LineStatus,
    NarrationPolicy,
)
from frame.domain.series.record import Episode
from frame.domain.series.script_revision import ScriptEdit, revise_script


@dataclass(frozen=True)
class ReviseEpisodeScriptCommand:
    episode_id: str
    edits: tuple[ScriptEdit, ...]


@dataclass(frozen=True)
class ReviseEpisodeScriptResult:
    episode: Episode
    changed_lines: tuple[str, ...] = ()
    changed_overlays: tuple[str, ...] = ()  # 대본을 따라 바뀐 자막
    rerecord: tuple[str, ...] = ()  # 다시 녹음할 줄 (붙여 둔 녹음이 이제 대본과 다르다)
    warnings: tuple[str, ...] = ()


class ReviseEpisodeScript:
    def __init__(
        self,
        themes: ThemeRepository,
        episodes: EpisodeRepository,
        uow_factory: UnitOfWorkFactory,
        store: ArtifactStore,
        audio: AudioEditor,
        policy: NarrationPolicy = DEFAULT_NARRATION_POLICY,
    ) -> None:
        self._themes = themes
        self._episodes = episodes
        self._uow_factory = uow_factory
        self._store = store
        self._audio = audio
        self._policy = policy

    def execute(self, command: ReviseEpisodeScriptCommand) -> ReviseEpisodeScriptResult:
        with self._episodes.lock(command.episode_id):
            return self._revise(command)

    def _revise(self, command: ReviseEpisodeScriptCommand) -> ReviseEpisodeScriptResult:
        episode = settle_pending(
            self._episodes, self._uow_factory, require_episode(self._episodes, command.episode_id)
        )
        record = self._themes.get(episode.theme_id)
        warnings: list[str] = []
        if record is None:
            warnings.append("테마를 찾을 수 없어 대본 줄만 고쳤습니다 (자막 규칙을 알 수 없습니다)")
        revision = revise_script(
            episode.plan,
            record.theme if record is not None else None,
            command.edits,
            max_tempo=self._policy.max_tempo,
        )
        if not revision.changed_lines:
            return ReviseEpisodeScriptResult(episode)
        warnings += revision.warnings
        updated = replace(episode, plan=revision.plan)
        rerecord: tuple[str, ...] = ()
        narration = episode.narration
        if narration is not None:
            changed = set(revision.changed_lines)
            rerecord = tuple(
                a.line_key
                for a in narration.alignments
                if a.line_key in changed and a.status is not LineStatus.MISSING
            )
            if rerecord:
                # 바뀐 줄의 녹음은 옛 대본을 읽은 것이다. 트랙에서 빼고 나머지로 트랙을 다시 만든다
                alignments = tuple(
                    LineAlignment(a.line_key, LineStatus.MISSING) if a.line_key in rerecord else a
                    for a in narration.alignments
                )
                sources = [self._store.get(uri) for uri in narration.recording_uris]
                updated = replace(
                    updated,
                    narration=narration_track(
                        self._store,
                        self._audio,
                        updated,
                        narration.recording_uris,
                        sources,
                        alignments,
                        self._policy,
                    ),
                )
        if revision.changed_overlays or rerecord:
            updated = replace(
                updated, render=None
            )  # 자막이나 소리가 바뀌었다: 완성본을 다시 만든다
        self._episodes.save(updated)
        return ReviseEpisodeScriptResult(
            updated,
            revision.changed_lines,
            revision.changed_overlays,
            rerecord,
            tuple(warnings),
        )
