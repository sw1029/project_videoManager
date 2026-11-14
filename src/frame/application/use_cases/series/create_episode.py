"""테마로 새 편을 만든다 (US-06, ADR-0026).

CreateEpisode
1. 기획기가 이번 편의 내용을 낸다 (직접 준 내용이 있으면 그것을 쓴다).
2. build_episode_plan 이 테마의 고정 요소와 합쳐 편의 계획(IR·녹음 대본·화면 문구)을 만든다.
3. 그 IR 로 프로젝트와 DRAFT 버전을 만든다. 생성은 지금까지의 흐름 그대로다 (POST /versions/{id}/generations).
4. 테마의 고정 출연에 기준 이미지가 있으면 그 편의 사용자 참조 이미지로 붙인다.
   사용자 참조가 있는 객체는 그 이미지가 모든 장면의 기준이 되므로 (ADR-0015), 편이 달라도 같은 모습으로 나온다.

AdoptAnchors
- 완성된 편(또는 FRAME 이 만든 원본 영상)의 버전이 뽑아 둔 정체성 기준 이미지를 테마의 고정 출연에 붙인다.
  외부 영상에서 온 테마는 처음 만든 편(PILOT)의 것만 받을 수 있다 (Theme.validate 가 막는다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from frame.application.ports.common import Clock, IdGenerator
from frame.application.ports.series.planning import EarlierEpisode, PlanningRequest, SeriesPlanner
from frame.application.ports.series.repositories import (
    EpisodeRepository,
    ThemeRecord,
    ThemeRepository,
)
from frame.application.ports.unit_of_work import UnitOfWorkFactory
from frame.domain.enums import ReferenceOrigin, VersionState
from frame.domain.errors import NotFound, ValidationError
from frame.domain.models import Project, ReferenceImage, VideoVersion
from frame.domain.series.episode import (
    DEFAULT_POLICY,
    EpisodeBrief,
    EpisodeContent,
    PlanPolicy,
    build_episode_plan,
)
from frame.domain.series.record import Episode
from frame.domain.series.theme import AnchorOrigin

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


@dataclass(frozen=True)
class AdoptAnchorsCommand:
    theme_id: str
    version_id: str  # 기준 이미지를 가져올 버전
    origin: AnchorOrigin = AnchorOrigin.PILOT
    # 테마의 고정 출연 키 -> 그 버전의 객체 키. 같은 키면 적지 않아도 된다
    key_map: Mapping[str, str] = field(default_factory=dict)
    overwrite: bool = False  # 이미 기준 이미지가 있는 출연도 바꾼다


@dataclass(frozen=True)
class AdoptAnchorsResult:
    record: ThemeRecord
    adopted: tuple[str, ...]  # 기준 이미지를 붙인 고정 출연


class AdoptAnchors:
    def __init__(
        self,
        themes: ThemeRepository,
        episodes: EpisodeRepository,
        uow_factory: UnitOfWorkFactory,
    ) -> None:
        self._themes = themes
        self._episodes = episodes
        self._uow_factory = uow_factory

    def execute(self, command: AdoptAnchorsCommand) -> AdoptAnchorsResult:
        record = self._themes.get(command.theme_id)
        if record is None:
            raise NotFound(
                f"테마 {command.theme_id} 을 찾을 수 없습니다", theme_id=command.theme_id
            )
        unknown = sorted(set(command.key_map) - {c.entity_key for c in record.theme.cast})
        if unknown:
            raise ValidationError(f"테마에 없는 고정 출연입니다: {unknown}")
        with self._uow_factory() as uow:
            if uow.versions.get(command.version_id) is None:
                raise NotFound(
                    f"버전 {command.version_id} 을 찾을 수 없습니다", version_id=command.version_id
                )
            references = uow.reference_images.list_by_version(command.version_id)
        if command.origin is AnchorOrigin.PILOT:
            # '처음 만든 편의 것' 이라는 규칙을 부른 쪽의 말에만 기대지 않는다: 그 테마의 편의 버전이어야 한다
            own = {
                version_id
                for episode in self._episodes.list_by_theme(command.theme_id)
                for version_id in (episode.version_id, *episode.history)
            }
            if command.version_id not in own:
                raise ValidationError(
                    "이 테마로 만든 편의 버전이 아닙니다. 처음 만든 편(PILOT)의 기준 이미지만 받습니다",
                    version_id=command.version_id,
                )
        theme = record.theme
        adopted: list[str] = []
        for member in record.theme.cast:
            if member.reference_image_uri and not command.overwrite:
                continue
            wanted = command.key_map.get(member.entity_key, member.entity_key)
            candidates = [r for r in references if r.entity_key == wanted]
            if not candidates:
                continue
            # 클립에서 뽑은 정체성 기준(외형 설명이 있다)을 사용자 참조보다 먼저 쓴다
            best = next((r for r in candidates if r.is_identity), candidates[0])
            theme = theme.with_reference(
                member.entity_key, best.image_uri, command.origin, best.description
            )
            adopted.append(member.entity_key)
        updated = replace(record, theme=theme)
        if adopted:
            self._themes.save(updated)
        return AdoptAnchorsResult(updated, tuple(adopted))
