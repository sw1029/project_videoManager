"""편 계획 — 테마로 만드는 영상 한 편의 계획 (ADR-0026).

맡는 개념: 테마의 고정 요소와 이번 편의 내용을 합친 것. 생성 조건(IR), 사람이 읽을 녹음 대본, 후반에 얹을 화면 문구.

모듈 지도
- plan           EpisodeBrief · EpisodeContent · EpisodePlan · ScriptLine · OverlayItem · PlanPolicy
- plan_building  build_episode_plan (고정 요소는 코드가 넣는다)
"""

from .plan import (
    DEFAULT_POLICY,
    DEFAULT_REGIONS,
    EpisodeBrief,
    EpisodeContent,
    EpisodePlan,
    OverlayItem,
    OverlayKind,
    PlanPolicy,
    ScriptLine,
    SlotContent,
)
from .plan_building import build_episode_plan

__all__ = [
    "DEFAULT_POLICY",
    "DEFAULT_REGIONS",
    "EpisodeBrief",
    "EpisodeContent",
    "EpisodePlan",
    "OverlayItem",
    "OverlayKind",
    "PlanPolicy",
    "ScriptLine",
    "SlotContent",
    "build_episode_plan",
]
