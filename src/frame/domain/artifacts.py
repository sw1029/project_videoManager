"""생성 산출물 값 객체 (장면 클립, 최종 영상). URI 는 ArtifactStore 가 해석한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GeneratedClip:
    scene_key: str
    uri: str
    duration_ms: int
    fingerprint: str  # 이 클립을 만든 생성 조건의 해시 (재사용/캐시 키)
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class VideoArtifact:
    uri: str
    duration_ms: int
