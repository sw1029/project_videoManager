"""테마로 새 편을 만든다 (US-06, ADR-0026).

1. 기획기가 이번 편의 내용을 낸다 (직접 준 내용이 있으면 그것을 쓴다).
2. build_episode_plan 이 테마의 고정 요소와 합쳐 편의 계획(IR·녹음 대본·화면 문구)을 만든다.
3. 그 IR 로 프로젝트와 DRAFT 버전을 만든다. 생성은 지금까지의 흐름 그대로다 (POST /versions/{id}/generations).
4. 테마의 고정 출연에 기준 이미지가 있으면 그 편의 사용자 참조 이미지로 붙인다.
   사용자 참조가 있는 객체는 그 이미지가 모든 장면의 기준이 되므로 (ADR-0015), 편이 달라도 같은 모습으로 나온다.
편의 순번은 테마의 키를 쥐고 매긴다 (기획하는 동안 다른 편이 생겨도 번호가 겹치지 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass

from frame.application.ports.common import Clock, IdGenerator
from frame.application.ports.series.planning import EarlierEpisode, PlanningRequest, SeriesPlanner
from frame.application.ports.series.repositories import (
    EpisodeRepository,
    ThemeRepository,
)
from frame.application.ports.unit_of_work import UnitOfWorkFactory
from frame.domain.enums import ReferenceOrigin, VersionState
from frame.domain.errors import NotFound
from frame.domain.models import Project, ReferenceImage, VideoVersion
from frame.domain.series.episode import (
    DEFAULT_POLICY,
    EpisodeBrief,
    EpisodeContent,
    PlanPolicy,
    build_episode_plan,
)
from frame.domain.series.record import Episode

MAX_PROJECT_TITLE = 255  # projects.title 열의 길이


@dataclass(frozen=True)
class CreateEpisodeCommand:
    theme_id: str
    brief: EpisodeBrief
    content: EpisodeContent | None = None  # 직접 준 내용. 없으면 기획기가 만든다


class CreateEpisode:
    def __init__(
        self,
        themes: ThemeRepository,
        episodes: EpisodeRepository,
        planner: SeriesPlanner,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        policy: PlanPolicy = DEFAULT_POLICY,
    ) -> None:
        self._themes = themes
        self._episodes = episodes
        self._planner = planner
        self._uow_factory = uow_factory
        self._ids = ids
        self._clock = clock
        self._policy = policy

    def execute(self, command: CreateEpisodeCommand) -> Episode:
        record = self._themes.get(command.theme_id)
        if record is None:
            raise NotFound(
                f"테마 {command.theme_id} 을 찾을 수 없습니다", theme_id=command.theme_id
            )
        theme, brief = record.theme, command.brief
        earlier = self._episodes.list_by_theme(theme.theme_id)
        content = command.content
        if content is None:
            content = self._planner.plan(
                PlanningRequest(
                    theme=theme,
                    brief=brief,
                    earlier=tuple(
                        EarlierEpisode(e.brief.title, e.brief.topic, dict(e.plan.variables))
                        for e in earlier
                    ),
                )
            )
        plan = build_episode_plan(theme, brief, content, self._policy)

        now = self._clock.now()
        with self._uow_factory() as uow:
            project = Project(
                project_id=self._ids.new_id("prj"),
                title=f"{theme.title} — {plan.title}"[:MAX_PROJECT_TITLE],
                created_at=now,
            )
            version = VideoVersion(
                version_id=self._ids.new_id("ver"),
                project_id=project.project_id,
                state=VersionState.DRAFT,
                created_at=now,
                source_prompt=_source_prompt(theme.title, brief),
                target_duration_ms=plan.ir.total_duration_ms,
                seed=brief.seed,
                ir=plan.ir,
            )
            uow.projects.add(project)
            uow.versions.add(version)
            for member in theme.references():
                if not plan.ir.has_entity(member.entity_key):
                    continue  # 이 편에 나오지 않는 고정 출연
                if member.reference_image_uri is None:  # references() 가 이미 걸렀다
                    continue
                uow.reference_images.add(
                    ReferenceImage(
                        reference_image_id=self._ids.new_id("img"),
                        version_id=version.version_id,
                        image_uri=member.reference_image_uri,
                        entity_key=member.entity_key,
                        origin=ReferenceOrigin.USER,
                        description=member.reference_description,
                    )
                )
            uow.commit()

        # 순번은 테마의 키를 쥐고 매긴다. 기획(오래 걸린다)하는 동안 다른 편이 생겨도 번호가 겹치지 않는다
        with self._episodes.lock(f"theme:{theme.theme_id}"):
            taken = [e.index for e in self._episodes.list_by_theme(theme.theme_id)]
            episode = Episode(
                episode_id=self._ids.new_id("epi"),
                theme_id=theme.theme_id,
                index=max(taken, default=0) + 1,
                project_id=project.project_id,
                version_id=version.version_id,
                brief=brief,
                plan=plan,
                created_at=now,
            )
            self._episodes.save(episode)
        return episode


def _source_prompt(theme_title: str, brief: EpisodeBrief) -> str:
    """버전에 남기는 원문: 어느 테마의 어떤 편인지 (사람이 읽는 기록)."""
    values = ", ".join(f"{key}={value}" for key, value in brief.variables.items())
    parts = [f"[테마: {theme_title}] {brief.title.strip()}"]
    if brief.topic.strip():
        parts.append(brief.topic.strip())
    if values:
        parts.append(f"({values})")
    return " ".join(parts)
