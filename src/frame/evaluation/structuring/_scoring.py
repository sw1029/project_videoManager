"""한 사례 채점: 모델 IR 을 골드와 견주어 맞춤(tp)·오탐(fp)·놓침(fn)과 장면 오차를 낸다.

- 객체: 필수 골드 객체 중 맞춘 것 / 골드에 없는 모델 객체
- 요구조건: (장면, 객체, 술어, 상대 객체)를 골드 좌표로 옮긴 뒤 값이 맞으면 맞춤 (값 비교는 _values).
  장면을 쪼개 같은 조건을 되풀이한 것은 하나로 센다 (쪼갠 것은 장면 수 지표가 잡는다)
- 장면: 장면 수가 같은지, 같으면 장면 경계의 평균 |Δms|. 전체 길이 오차도 낸다
- 등장 장면(보조): (장면, 객체) 쌍. 수정 전파 범위가 여기서 정해진다
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from frame.domain.ir import IntermediateRepresentation, RequirementSpec

from ._gold import GoldRequirement, GoldSpec
from ._matching import map_scenes, match_entities, project


@dataclass(frozen=True)
class Tally:
    """맞춤(tp)·오탐(fp)·놓침(fn) 개수. 분모가 0 인 비율은 0.0 이다."""

    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float:
        return ratio(self.tp, self.tp + self.fn)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def __add__(self, other: Tally) -> Tally:
        return Tally(self.tp + other.tp, self.fp + other.fp, self.fn + other.fn)


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True)
class ValueMismatch:
    """같은 자리의 조건을 냈지만 값이 골드와 다른 것."""

    key: str  # 골드 요구조건 키
    expected: str
    actual: str

    def __str__(self) -> str:
        return f"{self.key}: 기대 '{self.expected}', 결과 '{self.actual}'"


@dataclass(frozen=True)
class CaseScore:
    """한 브리프의 채점 결과. missed/spurious 는 회귀를 읽기 위한 목록이다 (골드 키 / 모델 키)."""

    case_id: str
    entities: Tally
    requirements: Tally
    appearances: Tally
    expected_scene_count: int
    predicted_scene_count: int
    boundary_error_ms: float | None
    duration_error_ms: int | None
    missed_entities: tuple[str, ...] = ()
    spurious_entities: tuple[str, ...] = ()
    missed_requirements: tuple[str, ...] = ()  # 아예 내지 않은 필수 조건
    mismatched_requirements: tuple[ValueMismatch, ...] = ()  # 냈지만 값이 틀린 조건
    spurious_requirements: tuple[str, ...] = ()  # 골드에 없는 조건
    error: str | None = None  # 구조화 실패

    @property
    def scene_count_match(self) -> bool:
        return self.error is None and self.expected_scene_count == self.predicted_scene_count


def score_case(case_id: str, gold: GoldSpec, predicted: IntermediateRepresentation) -> CaseScore:
    entity_map = match_entities(gold.entities, predicted.entities)  # 모델 키 → 골드 키
    scene_map = map_scenes(gold.ir, predicted)  # 모델 장면 키 → 골드 장면 키
    matched = set(entity_map.values())
    missed_entities = tuple(k for k in gold.required_entity_keys if k not in matched)
    spurious_entities = tuple(
        e.entity_key for e in predicted.entities if e.entity_key not in entity_map
    )
    requirements, missed, mismatched, spurious = _score_requirements(
        gold, predicted, scene_map, entity_map
    )
    return CaseScore(
        case_id,
        entities=Tally(
            tp=len(gold.required_entity_keys) - len(missed_entities),
            fp=len(spurious_entities),
            fn=len(missed_entities),
        ),
        requirements=requirements,
        appearances=_score_appearances(gold, predicted, scene_map, entity_map),
        expected_scene_count=len(gold.ir.scenes),
        predicted_scene_count=len(predicted.scenes),
        boundary_error_ms=_boundary_error(gold.ir, predicted),
        duration_error_ms=abs(predicted.total_duration_ms - gold.ir.total_duration_ms),
        missed_entities=missed_entities,
        spurious_entities=spurious_entities,
        missed_requirements=missed,
        mismatched_requirements=mismatched,
        spurious_requirements=spurious,
    )


def failed_case(case_id: str, gold: GoldSpec, error: str) -> CaseScore:
    """구조화가 실패한 사례: 필수 항목을 모두 놓친 것으로 센다 (오탐은 없다)."""
    required = tuple(r.key for r in gold.requirements if not r.optional)
    return CaseScore(
        case_id,
        entities=Tally(fn=len(gold.required_entity_keys)),
        requirements=Tally(fn=len(required)),
        appearances=Tally(fn=len(gold.required_appearances)),
        expected_scene_count=len(gold.ir.scenes),
        predicted_scene_count=0,
        boundary_error_ms=None,
        duration_error_ms=None,
        missed_entities=gold.required_entity_keys,
        missed_requirements=required,
        error=error,
    )


def _boundary_error(
    gold: IntermediateRepresentation, predicted: IntermediateRepresentation
) -> float | None:
    """장면 수가 같을 때 안쪽 경계(마지막 장면의 끝을 뺀 끝 시각)의 평균 |Δms|. 다르면 None."""
    if len(gold.scenes) != len(predicted.scenes):
        return None
    expected = [s.planned_end_ms for s in gold.scenes[:-1]]
    actual = [s.planned_end_ms for s in predicted.scenes[:-1]]
    if not expected:
        return 0.0
    return sum(abs(a - b) for a, b in zip(expected, actual, strict=True)) / len(expected)


def _score_requirements(
    gold: GoldSpec,
    predicted: IntermediateRepresentation,
    scene_map: Mapping[str, str],
    entity_map: Mapping[str, str],
) -> tuple[Tally, tuple[str, ...], tuple[ValueMismatch, ...], tuple[str, ...]]:
    by_context: dict[tuple[str, str | None, str | None], list[GoldRequirement]] = {}
    for requirement in gold.requirements:
        by_context.setdefault(requirement.context, []).append(requirement)

    answers: dict[str, list[RequirementSpec]] = {}  # 골드 조건 키 → 그 조건을 겨냥한 모델 조건
    spurious: dict[tuple[Any, ...], str] = {}  # 골드 좌표의 자리 → 처음 본 모델 조건 키
    for req in predicted.iter_requirements():
        context = (
            scene_map.get(req.scene_key),
            project(req.entity_key, entity_map),
            project(req.related_entity_key, entity_map),
        )
        target = _target(by_context.get(context, ()), req)
        if target is None:
            spurious.setdefault((*context, req.predicate), req.key)
        else:
            answers.setdefault(target.key, []).append(req)

    tp = fp = fn = 0
    missed: list[str] = []
    mismatched: list[ValueMismatch] = []
    for requirement in gold.requirements:
        given = answers.get(requirement.key, [])
        if not given:
            if not requirement.optional:
                fn += 1
                missed.append(requirement.key)
        elif any(requirement.matches(r.expected_value) for r in given):
            tp += 0 if requirement.optional else 1
        else:
            fp += 1
            fn += 0 if requirement.optional else 1
            mismatched.append(
                ValueMismatch(
                    requirement.key, requirement.spec.expected_value, given[0].expected_value
                )
            )
    fp += len(spurious)
    return Tally(tp, fp, fn), tuple(missed), tuple(mismatched), tuple(spurious.values())


def _target(
    candidates: Sequence[GoldRequirement], requirement: RequirementSpec
) -> GoldRequirement | None:
    """같은 자리(장면·객체·상대)의 골드 조건 중 이 모델 조건이 겨냥한 것. 술어가 같은 것이 먼저다."""
    for candidate in candidates:
        if candidate.spec.predicate == requirement.predicate:
            return candidate
    alternatives = [c for c in candidates if requirement.predicate in c.accept_predicates]
    for candidate in alternatives:
        if candidate.matches(requirement.expected_value):
            return candidate
    return alternatives[0] if alternatives else None


def _score_appearances(
    gold: GoldSpec,
    predicted: IntermediateRepresentation,
    scene_map: Mapping[str, str],
    entity_map: Mapping[str, str],
) -> Tally:
    expected = {(s.scene_key, key) for s in gold.ir.scenes for key in s.entity_keys}
    required = gold.required_appearances
    actual = {
        (scene_map[s.scene_key], project(key, entity_map))
        for s in predicted.scenes
        if s.scene_key in scene_map
        for key in s.entity_keys
    }
    return Tally(tp=len(actual & required), fp=len(actual - expected), fn=len(required - actual))
