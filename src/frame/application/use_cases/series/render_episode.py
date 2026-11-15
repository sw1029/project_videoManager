"""편의 완성본을 만든다 (US-06·US-01, ADR-0027): 장면 클립에 화면 문구를 얹고, 이어 붙이고, 녹음을 붙인다.

장면 단위로 합성한다 (ADR-0003 의 장면 단위 원칙을 후반 작업에도 그대로 쓴다).
- 문구가 없는 장면은 버전의 클립을 그대로 쓴다 (다시 인코딩하지 않는다).
- 문구가 있는 장면은 '클립 내용 + 그 장면의 문구 + 그리는 설정' 으로 정한 키에 합성 결과를 둔다.
  같은 키가 있으면 다시 만들지 않는다. 그래서 문구 하나를 고치면 그 장면만 다시 합성되고, 나머지 장면은 바이트 그대로다.
  글꼴 같은 설정을 바꾸면 문구가 있는 장면을 모두 다시 그린다 (깨진 글자를 글꼴을 바꿔 고치는 경우).
- 조립은 지금의 VideoAssembler 를 쓴다 (규격이 같으면 스트림 복사).
- 녹음 트랙이 있으면 마지막에 소리만 붙인다 (그림은 복사).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.assembler import VideoAssembler
from frame.application.ports.series.audio import AudioEditor
from frame.application.ports.series.overlay import OverlayRenderer
from frame.application.ports.series.repositories import EpisodeRepository
from frame.application.ports.unit_of_work import UnitOfWorkFactory
from frame.application.use_cases.series._pending_revision import require_episode, settle_pending
from frame.domain.artifacts import GeneratedClip
from frame.domain.enums import VersionState
from frame.domain.errors import MediaNotAvailable, NotFound
from frame.domain.hashing import canonical_hash
from frame.domain.series.record import Episode, RenderResult


@dataclass(frozen=True)
class RenderEpisodeResult:
    episode: Episode
    composed_now: tuple[str, ...]  # 이번에 새로 합성한 장면
    reused: tuple[str, ...]  # 앞서 합성해 둔 것을 다시 쓴 장면


class RenderEpisode:
    def __init__(
        self,
        episodes: EpisodeRepository,
        uow_factory: UnitOfWorkFactory,
        store: ArtifactStore,
        renderer: OverlayRenderer,
        assembler: VideoAssembler,
        audio: AudioEditor,
    ) -> None:
        self._episodes = episodes
        self._uow_factory = uow_factory
        self._store = store
        self._renderer = renderer
        self._assembler = assembler
        self._audio = audio

    def execute(self, episode_id: str) -> RenderEpisodeResult:
        # 편 문서는 읽고 -> 고쳐 -> 통째로 덮어쓴다. 합성하는 동안 다른 유스케이스가 한 수정이 지워지지 않게
        # 편의 키를 쥐고 지나간다 (감사 BTS-24)
        with self._episodes.lock(episode_id):
            return self._render(episode_id)

    def _render(self, episode_id: str) -> RenderEpisodeResult:
        episode = settle_pending(
            self._episodes, self._uow_factory, require_episode(self._episodes, episode_id)
        )
        with self._uow_factory() as uow:
            version = uow.versions.get(episode.version_id)
        if version is None:
            raise NotFound(
                f"버전 {episode.version_id} 을 찾을 수 없습니다", version_id=episode.version_id
            )
        plan = episode.plan
        missing = [s.scene_key for s in plan.ir.scenes if s.scene_key not in version.clips]
        if version.state is not VersionState.COMPLETED or missing:
            raise MediaNotAvailable(
                f"편의 영상이 아직 없습니다 (버전 {version.version_id} 상태 {version.state}, "
                f"클립 없는 장면 {missing})",
                version_id=version.version_id,
            )

        scene_uris: dict[str, str] = {}
        ordered: list[GeneratedClip] = []
        composed: list[str] = []
        composed_now: list[str] = []
        reused: list[str] = []
        for scene in plan.ir.scenes:
            clip = version.clips[scene.scene_key]
            overlays = plan.overlays_for(scene.scene_key)
            uri = clip.uri
            if overlays:
                data = self._store.get(clip.uri)
                digest = canonical_hash(
                    {
                        "clip": hashlib.sha256(data).hexdigest(),
                        "overlays": [item.to_dict() for item in overlays],
                        "renderer": self._renderer.signature,
                    }
                )[:16]
                suffix = PurePosixPath(clip.uri).suffix or ".bin"
                key = f"episodes/{episode.episode_id}/scenes/{scene.scene_key}-{digest}{suffix}"
                known = self._store.uri_for(key)
                if known is not None and self._store.exists(known):
                    uri = known
                    reused.append(scene.scene_key)
                else:
                    rendered = self._renderer.render(data, overlays)
                    uri = self._store.put(key, rendered, self._store.content_type(clip.uri))
                    composed_now.append(scene.scene_key)
                composed.append(scene.scene_key)
            scene_uris[scene.scene_key] = uri
            ordered.append(GeneratedClip(scene.scene_key, uri, clip.duration_ms, clip.fingerprint))

        # 조립기의 첫 인자(version_id)는 결과를 둘 자리의 이름으로만 쓰인다. 완성본은 버전이 아니라 편의
        # 산출물이라 편의 이름을 준다 (그 버전의 최종 영상을 덮어쓰지 않는다)
        video = self._assembler.assemble(f"episode-{episode.episode_id}", ordered)
        final_uri = video.uri
        track_uri = episode.narration.track_uri if episode.narration is not None else None
        with_narration = track_uri is not None
        if track_uri is not None:
            muxed = self._audio.mux(self._store.get(video.uri), self._store.get(track_uri))
            suffix = PurePosixPath(video.uri).suffix or ".bin"
            # 내용으로 이름을 짓는다: 편 문서가 가리키는 완성본의 내용은 바뀌지 않는다 (감사 BTS-D39)
            digest = hashlib.sha256(muxed).hexdigest()[:16]
            final_uri = self._store.put(
                f"episodes/{episode.episode_id}/final-{digest}{suffix}",
                muxed,
                self._store.content_type(video.uri),
            )
        updated = replace(
            episode,
            render=RenderResult(
                video_uri=final_uri,
                version_id=version.version_id,
                duration_ms=video.duration_ms,
                scene_uris=scene_uris,
                composed_scene_keys=tuple(composed),
                with_narration=with_narration,
            ),
        )
        self._episodes.save(updated)
        return RenderEpisodeResult(updated, tuple(composed_now), tuple(reused))
