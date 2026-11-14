"""편의 조건을 고친다 (US-01, ADR-0026·0027): 그 조건이 걸린 장면만 다시 만들고, 편의 계획도 새 조건을 따른다.

영상의 수정은 지금까지의 수정 버전 흐름 그대로다 (CreateRevision: CHANGE 는 값을 바꾸고, REPAIR 는 값을 그대로 두고
그 장면을 다시 맞춘다. 다시 만드는 것은 조건이 달라진 장면뿐이다, ADR-0003·0017). 여기서 더하는 것은 세 가지다.

- 편이 새 버전을 가리키게 한다. 예전 버전은 history 에 남는다. 완성본은 다시 만들어야 하므로 비운다.
- 편의 계획(EpisodePlan)의 IR 을 수정 버전의 IR 로 바꾼다. 계획과 영상의 조건이 어긋나면 화면 검수가 틀린 것을 견준다.
  화면 문구와 녹음 대본은 조건이 아니므로 그대로다.
- **테마가 고정한 것은 편에서 바꿀 수 없다.** 고정 출연의 고정 속성, 스타일·자리의 장면 조건을 다른 값으로 바꾸려는
  수정은 거절한다 (편을 만들 때 코드가 넣은 약속이다, ADR-0026). 값을 그대로 두고 다시 맞추는 것(REPAIR)은 된다.

화면 검수가 찾은 어긋남을 고치는 것은 CorrectEpisodeScreen 이 한다. 이 유스케이스는 사람이 조건을 정해 고칠 때 쓴다
(예: 손에 든 장면의 컵에도 글자를 건다).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from frame.domain.enums import RevisionKind
from frame.domain.errors import NotFound, ValidationError
from frame.domain.revision import KeepItem
from frame.domain.series import Episode
from frame.domain.services.propagation import AttributeChange, target_scenes
from frame.domain.theme import Theme

from ..dto import CreateRevisionCommand
from ..ports.theme_store import EpisodeRepository, ThemeRepository
from ..ports.unit_of_work import UnitOfWorkFactory
from ..use_cases.accept_generation import AcceptGeneration
from ..use_cases.create_revision import CreateRevision


@dataclass(frozen=True)
class ReviseEpisodeCommand:
    episode_id: str
    change: AttributeChange
    kind: RevisionKind = RevisionKind.CHANGE
    keep: tuple[KeepItem, ...] = ()  # 다시 만드는 장면에서 그대로 둘 요소
    request_text: str | None = None
    generate: bool = True  # 수정 버전의 생성을 바로 접수한다


class ReviseEpisode:
    def __init__(
        self,
        themes: ThemeRepository,
        episodes: EpisodeRepository,
        uow_factory: UnitOfWorkFactory,
        create_revision: CreateRevision,
        accept_generation: AcceptGeneration | None = None,
    ) -> None:
        self._themes = themes
        self._episodes = episodes
        self._uow_factory = uow_factory
        self._create_revision = create_revision
        self._accept_generation = accept_generation

    def execute(self, command: ReviseEpisodeCommand) -> Episode:
        episode = self._episodes.get(command.episode_id)
        if episode is None:
            raise NotFound(
                f"편 {command.episode_id} 을 찾을 수 없습니다", episode_id=command.episode_id
            )
        record = self._themes.get(episode.theme_id)
        if record is not None and command.kind is RevisionKind.CHANGE:
            _reject_fixed(record.theme, episode, command.change)

        created = self._create_revision.execute(
            CreateRevisionCommand(
                version_id=episode.version_id,
                change=command.change,
                kind=command.kind,
                keep=command.keep,
                request_text=command.request_text,
            )
        )
        with self._uow_factory() as uow:
            revision = uow.versions.get(created.version_id)
        assert revision is not None and revision.ir is not None  # 방금 만들었다
        plan = replace(episode.plan, ir=revision.ir)
        plan.validate()
        revised = replace(episode.with_version(created.version_id), plan=plan)
        self._episodes.save(revised)
        if command.generate and self._accept_generation is not None:
            self._accept_generation.execute(created.version_id)
        return revised


def _reject_fixed(theme: Theme, episode: Episode, change: AttributeChange) -> None:
    """테마가 고정한 조건을 다른 값으로 바꾸려는 수정이면 ValidationError."""
    ir = episode.plan.ir
    for scene_key in target_scenes(ir, change):
        if not any(slot.slot_key == scene_key for slot in theme.format.slots):
            continue
        for fixed in theme.fixed_requirements(scene_key, ir.scene(scene_key).entity_keys):
            same = (fixed.entity_key, fixed.predicate) == (change.entity_key, change.predicate)
            if same and fixed.expected_value != change.new_value:
                owner = f"'{fixed.entity_key}' 의" if fixed.entity_key else "장면의"
                raise ValidationError(
                    f"{owner} {fixed.predicate} = '{fixed.expected_value}' 는 테마가 고정한 조건입니다 "
                    f"(장면 '{scene_key}'). 편에서 바꿀 수 없습니다. 테마를 고치거나 새 테마를 만드세요",
                    scene_key=scene_key,
                    predicate=fixed.predicate,
                )
