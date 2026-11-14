"""편의 화면 정보·소리가 계획과 맞는지 본다 (US-03·US-01, ADR-0027). 어긋난 곳을 ScreenIssue 로 돌려준다.

보는 것
1. 영상의 요구조건: 기준 버전의 요구 반영 검수(REQUIREMENT)에서 FAIL 인 것.
   문구·브랜드는 SCENE_TEXT, 그 밖에는 ATTRIBUTE.
   일관성 검수(CONSISTENCY)에서 FAIL 인 객체는 기준과 다르게 보인 장면마다 CONSISTENCY.
2. 화면 문구 (계획): 문구 항목이 테마의 문구 틀에 편의 변수 값을 넣은 것과 같은가.
   기획이나 사람이 문구를 잘못 넣은 경우다 (가격을 틀리게 적음). 영상을 보지 않고 잡는다.
3. 화면 문구 (화면): 완성본의 그 장면·그 시각·그 영역에서 글자를 읽어, 얹으려던 문구와 같은가.
   글꼴에 글자가 없어 빈 네모로 나오거나, 영역 밖으로 잘린 경우다. 완성본이 있고 읽는 어댑터가 있을 때만 본다.
4. 녹음: 대본과 다르게 읽은 줄, 읽지 않은 줄, 장면보다 긴 줄.

읽기에 실패한 문구는 어긋난 곳으로 세지 않고 메모로 남긴다 (판정할 수 없는 것을 틀렸다고 하지 않는다).

가리고 다시 쓴 화면 속 글자 (PATCH)
- 클립은 그대로이므로 요구 반영 검수는 여전히 FAIL 이다. 요구한 글자로 가린 문구가 계획에 있으면
  그 요구조건은 어긋난 곳에서 빼고 patched 에 적는다. 빼지 않으면 고친 뒤에도 같은 것을 또 고치라고 한다.
- 가린 문구가 제대로 보이는지는 3번(화면에서 읽기)이 다른 문구와 같이 확인한다. 다르게 읽히면 그 문구가 어긋난 곳이다.
"""

from __future__ import annotations

import logging
from collections.abc import Set
from dataclasses import dataclass
from typing import Any

from frame.application.errors import AdapterError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.series.overlay import ScreenTextReader
from frame.application.ports.series.repositories import EpisodeRepository, ThemeRepository
from frame.application.ports.unit_of_work import UnitOfWorkFactory
from frame.application.use_cases.series._pending_revision import require_episode, settle_pending
from frame.domain.enums import VersionState
from frame.domain.errors import NotFound
from frame.domain.series.record import Episode
from frame.domain.series.screen_correction import (
    ScreenIssue,
    issues_from_checks,
    narration_issues,
    overlay_text_issue,
    plan_text_issues,
    scene_starts,
    screen_text_matches,
    without_patched,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScreenReport:
    episode_id: str
    version_id: str
    issues: tuple[ScreenIssue, ...]
    checked_requirements: int  # 요구 반영 검수 결과 수
    checked_overlays: int  # 화면에서 읽어 본 문구 수
    checked_lines: int  # 녹음을 맞춰 본 대본 줄 수
    notes: tuple[str, ...] = ()
    patched: tuple[str, ...] = ()  # 가리고 다시 써서 고친 요구조건 (어긋난 곳에서 뺐다)
    version_state: str = ""  # 기준 버전의 상태
    pending_version_id: str | None = None  # 만들고 있는 수정 버전 (끝나면 기준 버전이 된다)

    @property
    def ok(self) -> bool | None:
        """어긋난 곳이 없는가. 판정할 수 없으면 None 이다.

        기준 버전이 완성되지 않았거나 검수 결과가 하나도 없으면 '어긋난 곳 없음' 이 아니라 '모름' 이다.
        판정할 수 없는 것을 틀렸다고도, 맞다고도 하지 않는다 (감사 BTS-17).
        """
        if self.issues:
            return False
        if self.version_state != VersionState.COMPLETED or self.checked_requirements == 0:
            return None
        return True

    def issue(self, issue_id: str) -> ScreenIssue:
        for item in self.issues:
            if item.issue_id == issue_id:
                return item
        raise NotFound(f"어긋난 곳 '{issue_id}' 을 찾을 수 없습니다", issue_id=issue_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "version_id": self.version_id,
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "checked_requirements": self.checked_requirements,
            "checked_overlays": self.checked_overlays,
            "checked_lines": self.checked_lines,
            "notes": list(self.notes),
            "patched": list(self.patched),
            "version_state": self.version_state,
            "pending_version_id": self.pending_version_id,
        }


class CheckEpisodeScreen:
    def __init__(
        self,
        themes: ThemeRepository,
        episodes: EpisodeRepository,
        uow_factory: UnitOfWorkFactory,
        store: ArtifactStore,
        reader: ScreenTextReader | None = None,
    ) -> None:
        self._themes = themes
        self._episodes = episodes
        self._uow_factory = uow_factory
        self._store = store
        self._reader = reader

    def execute(self, episode_id: str) -> ScreenReport:
        with self._episodes.lock(episode_id):
            return self._check(episode_id)

    def _check(self, episode_id: str) -> ScreenReport:
        episode = settle_pending(
            self._episodes, self._uow_factory, require_episode(self._episodes, episode_id)
        )
        with self._uow_factory() as uow:
            version = uow.versions.get(episode.version_id)
            results = list(uow.checks.list_by_version(episode.version_id))
        notes: list[str] = []
        issues, patched = without_patched(
            episode.plan, issues_from_checks(episode.plan.ir, results)
        )
        issues += self._plan_text_issues(episode, notes)
        seen = {i.overlay_id for i in issues if i.overlay_id}
        read, shown = self._rendered_text_issues(episode, seen, notes)
        issues += shown
        issues += narration_issues(episode)
        return ScreenReport(
            episode_id=episode.episode_id,
            version_id=episode.version_id,
            issues=tuple(issues),
            checked_requirements=len(results),
            checked_overlays=read,
            checked_lines=len(episode.narration.alignments) if episode.narration else 0,
            notes=tuple(notes),
            patched=tuple(patched),
            version_state=str(version.state) if version is not None else "",
            pending_version_id=episode.pending_version_id,
        )

    # ---- 2. 문구 (계획) ----
    def _plan_text_issues(self, episode: Episode, notes: list[str]) -> list[ScreenIssue]:
        record = self._themes.get(episode.theme_id)
        if record is None:
            notes.append("테마를 찾을 수 없어 화면 문구를 문구 틀과 견주지 않았습니다")
            return []
        return plan_text_issues(record.theme, episode.plan)

    # ---- 3. 문구 (화면) ----
    def _rendered_text_issues(
        self, episode: Episode, skip: Set[str], notes: list[str]
    ) -> tuple[int, list[ScreenIssue]]:
        if not episode.plan.overlays:
            return 0, []
        render = episode.render
        if self._reader is None:
            notes.append(
                "화면의 글자를 읽는 어댑터가 없어 얹은 문구를 화면에서 확인하지 않았습니다"
            )
            return 0, []
        if render is None or render.version_id != episode.version_id:
            notes.append("완성본이 없어 얹은 문구를 화면에서 확인하지 않았습니다")
            return 0, []
        starts = scene_starts(episode.plan)
        issues: list[ScreenIssue] = []
        read = 0
        for scene_key in render.composed_scene_keys:
            clip = self._store.get(render.scene_uris[scene_key])
            for item in episode.plan.overlays_for(scene_key):
                at_ms = (item.start_ms + item.end_ms) // 2
                try:
                    seen = self._reader.read(clip, at_ms, (item.region,))[0]
                except (AdapterError, IndexError) as exc:
                    log.warning("화면 문구를 읽지 못했습니다 (%s): %s", item.overlay_id, exc)
                    notes.append(f"화면 문구 '{item.overlay_id}' 를 읽지 못했습니다: {exc}")
                    continue
                read += 1
                if item.overlay_id in skip or screen_text_matches(item.text, seen):
                    continue
                start = starts[scene_key]
                issues.append(
                    overlay_text_issue(
                        item,
                        start,
                        expected=item.text,
                        observed=seen,
                        detail={"source": "render", "read_at_ms": start + at_ms},
                    )
                )
        return read, issues
