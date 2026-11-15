"""편이 만들고 있는 수정 버전의 결말을 편에 반영한다 (ADR-0027, 감사 BTS-16).

상태를 갱신해 보여 줄 때와, 만들다 만 초안(생성을 접수하지 않은 수정 버전)을 버릴 때 쓴다.
초안을 버리지 않으면 그 초안이 다음 클립 수정을 막는다. 접수된 버전은 버리지 않는다 (끝나기를 기다린다).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from frame.application.ports.series.repositories import EpisodeRepository
from frame.application.ports.unit_of_work import UnitOfWorkFactory
from frame.application.use_cases.series._pending_revision import require_episode, settle_pending
from frame.domain.enums import VersionState
from frame.domain.series.record import Episode


@dataclass(frozen=True)
class SettleEpisodeCommand:
    episode_id: str
    # 만들다 만 초안(생성을 접수하지 않은 수정 버전)을 버린다. 버리지 않으면 그 초안이 다음 클립 수정을 막는다
    discard_draft: bool = False


class SettleEpisode:
    def __init__(self, episodes: EpisodeRepository, uow_factory: UnitOfWorkFactory) -> None:
        self._episodes = episodes
        self._uow_factory = uow_factory

    def execute(self, command: SettleEpisodeCommand) -> Episode:
        with self._episodes.lock(command.episode_id):
            episode = require_episode(self._episodes, command.episode_id)
            episode = settle_pending(self._episodes, self._uow_factory, episode)
            if command.discard_draft and episode.pending_version_id is not None:
                with self._uow_factory() as uow:
                    version = uow.versions.get(episode.pending_version_id)
                if version is None or version.state is VersionState.DRAFT:
                    episode = replace(episode, pending_version_id=None)
                    self._episodes.save(episode)
            return episode
