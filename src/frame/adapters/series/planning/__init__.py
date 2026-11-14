"""편 기획 어댑터 (포트 ports/series/planning.py, ADR-0026).

맡는 개념: 테마와 편 요청에서 이번 편의 내용(편마다 다른 것만)을 낸다.
고정 요소는 도메인의 build_episode_plan 이 넣는다.
밖에서 쓰는 이름: TemplateSeriesPlanner. LLM 구현은 조립이 고를 때 import 한다.

모듈 지도
- template  TemplateSeriesPlanner: 모델 없이 자리의 구실과 요청의 내용으로 채운다 (결정적)
- llm       LLMSeriesPlanner: 테마·앞선 편을 보여 주고 이번 편의 내용을 받는다. 규칙에 걸리면 다시 묻는다
"""

from frame.adapters.series.planning.template import TemplateSeriesPlanner

__all__ = ["TemplateSeriesPlanner"]
