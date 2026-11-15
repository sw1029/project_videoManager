"""편이 만들고 있는 수정 버전의 결말을 편에 반영한다 (감사 BTS-16·17).

수정 버전을 만들면 편은 그 버전을 pending_version_id 로만 적는다 (Episode.with_pending). 기준 버전은 그대로다.
- 그 버전이 끝나면(COMPLETED) 기준 버전이 되고, 계획의 IR 도 그 버전의 것으로 바뀐다.
  그 버전에서 클립이 바뀐 장면의 가린 문구(PATCH)는 계획에서 빠진다 (감사 BTS-D01).
- 실패했거나 없어졌으면 잊는다. 기준 버전은 그대로라 완성본·검수·수정을 계속 할 수 있다.
- 초안이거나 만드는 중이면 그대로 둔다.
- 끝났는데 그 버전의 IR 을 편의 계획이 따를 수 없으면 (편 밖에서 고친 초안) 기준 버전으로 삼지 않고 잊는다 (감사 P-20).

그 전에 편의 계획과 기준 버전의 조건이 같은지 본다. 편의 버전도 프로젝트의 버전이라, 초안인 동안에는 일반 IR 편집
(PUT /versions/{id}/ir, #11)이 닿는다. 편의 계획은 그 편집을 모른다 — 화면 문구와 대본은 계획의 장면에 맞춰져 있고,
화면 검수는 계획의 조건으로 견준다. 어긋난 채로 쓰면 없는 장면의 클립을 기다리거나, 만들지 않은 조건으로 견주고도
통과로 답한다. 그래서 달라졌으면 계획이 버전의 IR 을 따르고 (장면의 짜임이 같을 때 — EpisodePlan.following),
따를 수 없으면 IntegrityViolation 으로 알린다 (감사 P-20).

끝나기 전에 기준 버전을 옮기면, 실패했을 때 편이 완성본도 검수도 수정도 안 되는 버전에 멈춘다.
버전이 끝났다는 알림을 따로 받지 않는다. 편을 읽는 유스케이스가 들어올 때마다 settle_pending 을 부른다
(완성본 만들기 · 화면 검수 · 화면 수정 · 편 수정 · SettleEpisode). 유스케이스들이 함께 쓰는 안쪽 모듈이다.
"""

from __future__ import annotations

from frame.application.ports.series.repositories import EpisodeRepository
from frame.application.ports.unit_of_work import UnitOfWorkFactory
from frame.domain.enums import VersionState
from frame.domain.errors import IntegrityViolation, NotFound, ValidationError
from frame.domain.models import VideoVersion
from frame.domain.series.record import Episode


def settle_pending(
    episodes: EpisodeRepository, uow_factory: UnitOfWorkFactory, episode: Episode
) -> Episode:
    """만들고 있던 수정 버전이 끝났으면 편에 반영해 저장하고, 반영한 편을 돌려준다. 편의 키를 쥐고 부른다.

    그 전에 기준 버전의 IR 이 편 밖에서 바뀌었는지 본다. 계획이 따를 수 있으면 따른 편을 저장하고,
    없으면 IntegrityViolation 이다.
    """
    with uow_factory() as uow:
        base = uow.versions.get(episode.version_id)
        version = (
            uow.versions.get(episode.pending_version_id)
            if episode.pending_version_id is not None
            else None
        )
    settled = _following_base(episode, base)
    if settled.pending_version_id is not None:
        done = version is not None and version.state is VersionState.COMPLETED
        settled = settled.settled(
            version.state if version is not None else None,
            version.ir if version is not None else None,
            remade_scene_keys=_remade_scenes(base, version) if done else (),
        )
    if settled != episode:
        episodes.save(settled)
    return settled


def _following_base(episode: Episode, base: VideoVersion | None) -> Episode:
    """기준 버전의 IR 이 계획의 것과 다르면 계획이 그것을 따른 편. 따를 수 없으면 IntegrityViolation.

    편을 만들 때 계획의 IR 이 그대로 버전의 IR 이 되고, 수정 버전이 기준이 될 때 계획이 그 IR 을 따른다.
    그래서 편의 유스케이스만 쓰면 둘은 늘 같다. 다르다면 버전의 IR 을 편 밖에서 고친 것이다.
    버전이 없어졌으면 여기서 따지지 않는다 (그 버전을 쓰는 유스케이스가 알린다).
    """
    if base is None or base.ir is None or base.ir.to_dict() == episode.plan.ir.to_dict():
        return episode
    try:
        return episode.following_base(base.ir)
    except ValidationError as exc:
        raise IntegrityViolation(
            f"편 {episode.episode_id} 의 기준 버전 {base.version_id} 의 중간표현을 편 밖에서 고쳤는데 "
            f"(일반 IR 편집), 편의 계획이 그것을 따를 수 없습니다 ({exc.message}). "
            "편의 화면 문구와 대본은 계획의 장면에 맞춰져 있습니다. "
            "버전의 중간표현을 되돌리거나 편을 새로 만드세요",
            episode_id=episode.episode_id,
            version_id=base.version_id,
        ) from exc


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
