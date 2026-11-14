"""시리즈 기획 어댑터 (ADR-0026). LLM 구현은 레지스트리가 필요할 때 import 한다."""

from frame.adapters.series.planning.template import TemplateSeriesPlanner

__all__ = ["TemplateSeriesPlanner"]
