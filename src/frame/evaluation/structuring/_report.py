"""사람이 읽는 표: 기록 하나의 요약과 두 기록의 비교."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ._records import RecordComparison, record_label

_LABELS = {
    "entity_recall": "객체 재현율",
    "entity_precision": "객체 정밀도",
    "requirement_precision": "요구조건 정밀도",
    "requirement_recall": "요구조건 재현율",
    "requirement_f1": "요구조건 F1",
    "appearance_f1": "등장 장면 F1",
    "scene_count_match_rate": "장면 수 일치",
    "mean_boundary_error_ms": "경계 오차(ms)",
    "mean_duration_error_ms": "길이 오차(ms)",
}


def format_summary(record: Mapping[str, Any]) -> str:
    summary = record["summary"]
    targets = summary.get("targets", {})
    lines = [
        f"구조화 골드셋 | {record_label(record)} ({summary['cases']}건, 실패 {summary['failures']})",
    ]
    for name, label in _LABELS.items():
        target = f" (목표 ≥ {targets[name]})" if name in targets else ""
        lines.append(f"  {label:<12} {_fmt(summary.get(name))}{target}")
    lines.append(f"  목표 충족: {'예' if summary['meets_target'] else '아니오'}")
    if summary.get("greedy_entity_matchings"):
        lines.append(
            "  ※ 객체 짝을 차례로 고른 사례 (대응 수가 상한을 넘음, 가장 좋은 짝을 놓쳤을 수 있다): "
            + _join(summary["greedy_entity_matchings"])
        )
    lines.append("  사례 (요구조건 F1 | 놓침 | 값 틀림 | 골드에 없음 | 객체 놓침·남음):")
    for case in record["cases"]:
        if case["error"]:
            lines.append(f"    {case['case_id']}: 실패 {case['error']}")
            continue
        mismatched = [f"{m['key']}={m['actual']!r}" for m in case["mismatched_requirements"]]
        entities = case["missed_entities"] + [f"+{k}" for k in case["spurious_entities"]]
        lines.append(
            f"    {case['case_id']} {_fmt(case['requirement_f1'])} | "
            f"{_join(case['missed_requirements'])} | {_join(mismatched)} | "
            f"{_join(case['spurious_requirements'])} | {_join(entities)}"
        )
    return "\n".join(lines)


def format_comparison(comparison: RecordComparison) -> str:
    lines = [f"기준 {comparison.baseline}", f"비교 {comparison.candidate}"]
    if not comparison.same_gold:
        lines.append(
            "  ※ 골드셋이 다르다. 기준 기록의 원본 응답을 지금 골드로 다시 채점한 뒤 견줄 것 "
            "(recorded_responses + ReplayLLMClient)"
        )
    for change in comparison.metrics:
        mark = "  ← 회귀" if change.regressed else ""
        delta = "" if change.delta is None else f"{change.delta:+.4f}"
        lines.append(
            f"  {change.name:<24} {_fmt(change.baseline):>9} → {_fmt(change.candidate):>9} "
            f"{delta:>9}{mark}"
        )
    if comparison.cases:
        lines.append("  달라진 사례:")
    lines.extend(
        f"    {case.case_id} F1 {_fmt(case.baseline_f1)} → {_fmt(case.candidate_f1)}"
        f" | 새로 놓침: {_join(case.newly_missed)} | 새로 맞춤: {_join(case.newly_found)}"
        for case in comparison.cases
    )
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") if value % 1 else f"{value:.0f}"
    return str(value)


def _join(items: Sequence[str]) -> str:
    return ", ".join(items) if items else "-"
