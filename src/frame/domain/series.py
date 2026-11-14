"""시리즈의 편 (ADR-0026): 테마 하나로 만든 편의 기록.

편은 FRAME 의 프로젝트·버전 위에 후반 작업을 얹은 것이다.
- 영상(그림): 버전의 장면 클립. 화면 속성을 고치면 수정 버전이 생기고 편은 그 버전을 가리킨다 (history 에 지나온 버전).
- 화면 문구: 계획의 OverlayItem 을 장면 클립에 얹는다 (합성).
- 소리: 사용자가 녹음한 음성을 대본 줄에 맞춰 놓은 트랙.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from .episode import EpisodeBrief, EpisodePlan
from .services.narration import LineAlignment, LineStatus, Placement, PlacementStatus


@dataclass(frozen=True)
class NarrationResult:
    """녹음을 붙인 결과: 어느 녹음을, 줄마다 어디에 맞추고, 어디에 놓았는가."""

    recording_uris: tuple[str, ...]
    alignments: tuple[LineAlignment, ...]
    placements: tuple[Placement, ...]
    track_uri: str | None = None  # 편 길이의 소리 트랙

    @property
    def problem_lines(self) -> tuple[str, ...]:
        """다시 볼 줄: 대본과 다르게 읽었거나, 읽지 않았거나, 장면보다 긴 줄."""
        bad = {a.line_key for a in self.alignments if a.status is not LineStatus.MATCHED}
        bad |= {
            p.line_key
            for p in self.placements
            if p.status in (PlacementStatus.OVERFLOW, PlacementStatus.MISSING)
        }
        return tuple(a.line_key for a in self.alignments if a.line_key in bad)

    @property
    def ok(self) -> bool:
        return not self.problem_lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "recording_uris": list(self.recording_uris),
            "alignments": [a.to_dict() for a in self.alignments],
            "placements": [p.to_dict() for p in self.placements],
            "track_uri": self.track_uri,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NarrationResult:
        return cls(
            recording_uris=tuple(data.get("recording_uris", ())),
            alignments=tuple(LineAlignment.from_dict(a) for a in data.get("alignments", ())),
            placements=tuple(Placement.from_dict(p) for p in data.get("placements", ())),
            track_uri=data.get("track_uri"),
        )


@dataclass(frozen=True)
class RenderResult:
    """편의 완성본: 문구를 얹은 장면을 이어 붙이고 소리를 붙인 영상."""

    video_uri: str
    version_id: str  # 어느 버전의 장면 클립으로 만들었는가
    duration_ms: int
    scene_uris: Mapping[str, str] = field(default_factory=dict)  # 장면 -> 합성한(또는 원래) 클립
    composed_scene_keys: tuple[str, ...] = ()  # 문구를 얹은 장면
    with_narration: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_uri": self.video_uri,
            "version_id": self.version_id,
            "duration_ms": self.duration_ms,
            "scene_uris": dict(self.scene_uris),
            "composed_scene_keys": list(self.composed_scene_keys),
            "with_narration": self.with_narration,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RenderResult:
        return cls(
            video_uri=str(data["video_uri"]),
            version_id=str(data["version_id"]),
            duration_ms=int(data["duration_ms"]),
            scene_uris={str(k): str(v) for k, v in (data.get("scene_uris") or {}).items()},
            composed_scene_keys=tuple(data.get("composed_scene_keys", ())),
            with_narration=bool(data.get("with_narration", False)),
        )


@dataclass(frozen=True)
class Episode:
    episode_id: str
    theme_id: str
    index: int  # 시리즈 안에서의 순번 (1부터)
    project_id: str
    version_id: str  # 지금 기준이 되는 영상 버전
    brief: EpisodeBrief
    plan: EpisodePlan
    created_at: datetime
    narration: NarrationResult | None = None
    render: RenderResult | None = None
    history: tuple[str, ...] = ()  # 지나온 버전 (화면 속성을 고치기 전의 버전들)

    def with_version(self, version_id: str) -> Episode:
        """기준 버전을 바꾼다 (수정 버전으로 넘어갈 때). 완성본은 다시 만들어야 하므로 비운다."""
        if version_id == self.version_id:
            return self
        return replace(
            self, version_id=version_id, history=(*self.history, self.version_id), render=None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "theme_id": self.theme_id,
            "index": self.index,
            "project_id": self.project_id,
            "version_id": self.version_id,
            "brief": self.brief.to_dict(),
            "plan": self.plan.to_dict(),
            "created_at": self.created_at.isoformat(),
            "narration": self.narration.to_dict() if self.narration is not None else None,
            "render": self.render.to_dict() if self.render is not None else None,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Episode:
        narration, render = data.get("narration"), data.get("render")
        return cls(
            episode_id=str(data["episode_id"]),
            theme_id=str(data["theme_id"]),
            index=int(data["index"]),
            project_id=str(data["project_id"]),
            version_id=str(data["version_id"]),
            brief=EpisodeBrief.from_dict(data["brief"]),
            plan=EpisodePlan.from_dict(data["plan"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            narration=NarrationResult.from_dict(narration) if narration is not None else None,
            render=RenderResult.from_dict(render) if render is not None else None,
            history=tuple(data.get("history", ())),
        )
