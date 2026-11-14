"""틀 채우기 기획 (결정적, LLM 없음): 테마의 틀에 이번 편의 변수 값만 바꿔 넣는다 (ADR-0026).

자리마다
- 장면 설명: 자리의 구실. 원본의 변수 값이 들어 있으면 이번 편의 값으로 바꾼다.
- 말할 문장: 테마가 원본에서 받아 적은 말(내레이션의 예)에서 원본의 변수 값을 이번 편의 값으로 바꾼다.
  예) "바닐라 라떼입니다." + {menu: 말차 라떼} -> "말차 라떼입니다."
내용을 새로 지어내지는 못한다 (새 객체·새 조건·자리별 자막 없음). 테스트와 오프라인 시연,
그리고 '같은 형식에 값만 바꾼 편' 의 기준선으로 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping

from frame.application.ports.series import PlanningRequest, SeriesPlanner
from frame.domain.episode import EpisodeContent, SlotContent, build_episode_plan
from frame.domain.theme import Theme, VariableScope


class TemplateSeriesPlanner(SeriesPlanner):
    def __init__(self) -> None:
        self.requests: list[PlanningRequest] = []  # 스파이

    def plan(self, request: PlanningRequest) -> EpisodeContent:
        self.requests.append(request)
        theme, brief = request.theme, request.brief
        swaps = _swaps(theme, brief.variables)
        slots = []
        for slot in theme.format.slots:
            beat = theme.narration.beat(slot.slot_key)
            slots.append(
                SlotContent(
                    slot_key=slot.slot_key,
                    description=_swap(slot.purpose, swaps),
                    narration=_swap(beat.example, swaps) if beat is not None else "",
                )
            )
        content = EpisodeContent(slots=tuple(slots))
        build_episode_plan(theme, brief, content)  # 변수가 모자라면 여기서 ValidationError
        return content


def _swaps(theme: Theme, values: Mapping[str, str]) -> list[tuple[str, str]]:
    """(원본의 값, 이번 편의 값) 쌍. 긴 값부터 바꿔 겹치는 값이 서로를 깨뜨리지 않게 한다."""
    pairs = [
        (variable.example.strip(), str(values[variable.key]).strip())
        for variable in theme.variables
        if variable.scope is VariableScope.EPISODE
        and variable.example.strip()
        and str(values.get(variable.key, "")).strip()
    ]
    return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)


def _swap(text: str, swaps: list[tuple[str, str]]) -> str:
    for old, new in swaps:
        text = text.replace(old, new)
    return text
