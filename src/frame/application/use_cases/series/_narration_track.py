"""녹음을 줄에 맞춘 결과로 편 길이의 소리 트랙을 만든다.

녹음 붙이기(AttachNarration)와 편의 대본 수정(ReviseEpisodeScript)이 함께 쓰는 안쪽 모듈이다.
줄마다의 맞춤(alignments)이 정해지면, 장면에 놓고(place_lines) 조각을 이어 트랙 하나로 만든다.
맞춤이 없는 줄(MISSING)의 자리는 무음이다.
트랙의 키에는 내용의 해시가 든다: 편 문서가 가리키는 트랙의 내용은 바뀌지 않는다. 고정 키면 트랙을 쓴 뒤 편 문서
저장이 실패했을 때 문서(옛 맞춤 기록)와 트랙(새 내용)이 어긋난다 (감사 BTS-D08·D39, 녹음 파일의 BTS-23 과 같다).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.series.audio import AudioEditor, TrackCut
from frame.domain.errors import ValidationError
from frame.domain.series.narration import (
    LineAlignment,
    NarrationPolicy,
    PlacementStatus,
    place_lines,
)
from frame.domain.series.record import Episode, NarrationResult


def narration_track(
    store: ArtifactStore,
    audio: AudioEditor,
    episode: Episode,
    uris: Sequence[str],
    sources: Sequence[bytes],
    alignments: Sequence[LineAlignment],
    policy: NarrationPolicy,
) -> NarrationResult:
    """sources 는 uris 가 가리키는 녹음의 내용이다 (같은 순서). 트랙을 저장하고 결과를 돌려준다."""
    durations = [_duration(audio, data) for data in sources]
    spans = {
        scene.scene_key: (scene.planned_start_ms, scene.planned_end_ms)
        for scene in episode.plan.ir.scenes
    }
    placements = place_lines(episode.plan.script, alignments, spans, durations, policy)
    cuts = [
        TrackCut(p.source_index, p.source_start_ms, p.source_end_ms, p.target_start_ms, p.tempo)
        for p in placements
        if p.status is not PlacementStatus.MISSING
    ]
    track = audio.render_track(sources, cuts, episode.plan.ir.total_duration_ms)
    digest = hashlib.sha256(track).hexdigest()[:16]
    track_uri = store.put(
        f"episodes/{episode.episode_id}/narration-{digest}{audio.track_suffix}", track
    )
    return NarrationResult(tuple(uris), tuple(alignments), tuple(placements), track_uri)


def _duration(audio: AudioEditor, data: bytes) -> int:
    info = audio.probe(data)
    if info is None:
        raise ValidationError("녹음 파일에 소리가 없습니다")
    return info.duration_ms
