"""시리즈 기획 포트 (US-06, ADR-0026): 테마와 이번 편의 요청에서 그 편의 내용을 만든다.

기획은 편마다 다른 것만 낸다 (자리마다의 장면 설명·그 편의 객체와 조건·말할 문장·자리별 문구 값).
테마의 고정 요소는 build_episode_plan 이 넣으므로, 기획이 그것을 빠뜨리거나 바꿀 수 없다.
구현: 틀 채우기(결정적, 테스트·오프라인), LLM.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field

from frame.application.errors import AdapterError
from frame.domain.series.episode import EpisodeBrief, EpisodeContent
from frame.domain.series.theme import Theme


class SeriesPlanningError(AdapterError):
    code = "SERIES_PLANNING_ERROR"


@dataclass(frozen=True)
class EarlierEpisode:
    """같은 테마로 이미 만든 편. 기획이 내용을 겹치지 않게 하는 데 쓴다."""

    title: str
    topic: str = ""
    variables: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanningRequest:
    theme: Theme
    brief: EpisodeBrief
    earlier: tuple[EarlierEpisode, ...] = ()


class SeriesPlanner(ABC):
    """계약
    - 돌려준 내용은 build_episode_plan(theme, brief, content) 를 통과한다:
      테마의 자리를 모두, 그 자리만 채우고, 화면 문구에 쓰는 편 단위 변수에 값이 있다.
    - 요청이 준 변수의 값을 바꾸지 않는다.
    - 응답을 규칙에 맞게 다듬지 못한 실패는 SeriesPlanningError 다.
    - LLM 을 쓰는 구현에서 LLM 호출의 오류(LLMTimeout · LLMUnavailable · LLMRateLimited · LLMBadResponse)는
      바꾸지 않고 그대로 올린다. 부른 쪽이 종류를 가려 다시 시도할 수 있다.
    """

    @abstractmethod
    def plan(self, request: PlanningRequest) -> EpisodeContent: ...
