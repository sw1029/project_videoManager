"""편이 만들고 있는 수정 버전의 결말을 편에 반영한다 (감사 BTS-16·17).

수정 버전을 만들면 편은 그 버전을 pending_version_id 로만 적는다 (Episode.with_pending). 기준 버전은 그대로다.
- 그 버전이 끝나면(COMPLETED) 기준 버전이 되고, 계획의 IR 도 그 버전의 것으로 바뀐다.
  그 버전에서 클립이 바뀐 장면의 가린 문구(PATCH)는 계획에서 빠진다 (감사 BTS-D01).
- 실패했거나 없어졌으면 잊는다. 기준 버전은 그대로라 완성본·검수·수정을 계속 할 수 있다.
- 초안이거나 만드는 중이면 그대로 둔다.

끝나기 전에 기준 버전을 옮기면, 실패했을 때 편이 완성본도 검수도 수정도 안 되는 버전에 멈춘다.
버전이 끝났다는 알림을 따로 받지 않는다. 편을 읽는 유스케이스가 들어올 때마다 settle_pending 을 부른다
(완성본 만들기 · 화면 검수 · 화면 수정 · 편 수정 · SettleEpisode). 유스케이스들이 함께 쓰는 안쪽 모듈이다.
"""

from __future__ import annotations

from frame.application.ports.series.repositories import EpisodeRepository
from frame.application.ports.unit_of_work import UnitOfWorkFactory
from frame.domain.enums import VersionState
from frame.domain.errors import NotFound
from frame.domain.models import VideoVersion
from frame.domain.series.record import Episode


def settle_pending(
    episodes: EpisodeRepository, uow_factory: UnitOfWorkFactory, episode: Episode
) -> Episode:
    """만들고 있던 수정 버전이 끝났으면 편에 반영해 저장하고, 반영한 편을 돌려준다. 편의 키를 쥐고 부른다."""
    if episode.pending_version_id is None:
        return episode
    with uow_factory() as uow:
        version = uow.versions.get(episode.pending_version_id)
        done = version is not None and version.state is VersionState.COMPLETED
        base = uow.versions.get(episode.version_id) if done else None
    settled = episode.settled(
        version.state if version is not None else None,
        version.ir if version is not None else None,
        remade_scene_keys=_remade_scenes(base, version) if done else (),
    )
    if settled != episode:
        episodes.save(settled)
    return settled


def _remade_scenes(base: VideoVersion | None, revision: VideoVersion | None) -> tuple[str, ...]:
    """수정 버전에서 클립이 바뀐 장면 (없어지거나 새로 생긴 장면 포함).

    다시 만들지 않은 장면은 부모의 클립을 그대로 쓴다 (주소가 같다). 견줄 기준 버전이 없으면 모든 장면이
    바뀐 것으로 본다: 낡은 가린 문구를 남기지 않는 쪽이 안전하다 (남은 글자는 검수가 다시 알린다).
    """
    before = base.clips if base is not None else {}
    after = revision.clips if revision is not None else {}
    return tuple(
        key
        for key in dict.fromkeys((*before, *after))
        if key not in before or key not in after or before[key].uri != after[key].uri
    )


def require_episode(episodes: EpisodeRepository, episode_id: str) -> Episode:
    episode = episodes.get(episode_id)
    if episode is None:
        raise NotFound(f"편 {episode_id} 을 찾을 수 없습니다", episode_id=episode_id)
    return episode
