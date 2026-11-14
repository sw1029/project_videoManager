"""짝 맞추기: 모델 객체를 골드 객체에, 모델 장면을 골드 장면에 붙인다.

키 이름은 모델·프롬프트마다 달라지므로(`red_mug` / `cup`, `scene_1` / `smile`) 객체는 종류와 설명(골드의 별칭)으로,
장면은 시간 겹침으로 맞춘다. 채점(_scoring)은 이 대응으로 모델의 요구조건을 골드 좌표로 옮긴다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from frame.domain.ir import EntitySpec, IntermediateRepresentation

from ._gold import GoldEntity
from ._values import stem, words


def match_entities(gold: Sequence[GoldEntity], predicted: Sequence[EntitySpec]) -> dict[str, str]:
    """모델 객체 키 → 골드 객체 키 (일대일).

    종류가 같고 별칭이 하나라도 키·설명에 있는 짝만 후보다. (필수 짝 수, 선택 짝 수, 맞은 별칭 수) 가 가장 큰
    대응을 고른다. 객체가 브리프당 열 개를 넘지 않고 후보가 적어 모두 훑어도 빠르다.
    """
    candidates = [
        sorted(
            (
                (hits, j)
                for j, entity in enumerate(predicted)
                if g.accepts(entity.kind) and (hits := _alias_hits(g.aliases, entity)) > 0
            ),
            key=lambda c: (-c[0], c[1]),
        )
        for g in gold
    ]
    order = sorted(range(len(gold)), key=lambda i: gold[i].optional)  # 필수 객체 먼저
    best_value = (0, 0, 0)
    best: dict[int, int] = {}

    def search(pos: int, used: frozenset[int], chosen: dict[int, int], value: tuple) -> None:
        nonlocal best_value, best
        if pos == len(order):
            if value > best_value:
                best_value, best = value, dict(chosen)
            return
        i = order[pos]
        required, optional, score = value
        for hits, j in candidates[i]:
            if j not in used:
                chosen[i] = j
                gain = (
                    required + (not gold[i].optional),
                    optional + gold[i].optional,
                    score + hits,
                )
                search(pos + 1, used | {j}, chosen, gain)
                del chosen[i]
        search(pos + 1, used, chosen, value)

    search(0, frozenset(), {}, best_value)
    return {predicted[j].entity_key: gold[i].entity_key for i, j in best.items()}


def _alias_hits(aliases: Iterable[str], entity: EntitySpec) -> int:
    """키도 설명의 하나로 읽는다 (키가 골드와 같을 필요는 없다)."""
    text = words(f"{entity.entity_key.replace('_', ' ')} {entity.description}")
    stems = {stem(w) for w in text}
    return sum(1 for alias in aliases if _alias_found(words(alias), text, stems))


def _alias_found(alias: list[str], text: list[str], stems: set[str]) -> bool:
    if not alias:
        return False
    if all(w.isascii() for w in alias):
        return {stem(w) for w in alias} <= stems
    if len(alias) == 1:
        # 한국어 복합어는 머리말이 뒤에 온다 (머그컵 → 컵). 앞부분만 같은 말(컵받침)은 다른 물건이다
        return any(w.endswith(alias[0]) for w in text)
    return f" {' '.join(alias)} " in f" {' '.join(text)} "


def map_scenes(
    gold: IntermediateRepresentation, predicted: IntermediateRepresentation
) -> dict[str, str]:
    """모델 장면 → 시간이 가장 많이 겹치는 골드 장면. 모델 타임라인을 골드 길이에 맞춰 늘이거나 줄여 견준다."""
    if not gold.scenes or not predicted.scenes or predicted.total_duration_ms <= 0:
        return {}
    scale = gold.total_duration_ms / predicted.total_duration_ms
    mapping: dict[str, str] = {}
    for scene in predicted.scenes:
        start, end = scene.planned_start_ms * scale, scene.planned_end_ms * scale
        overlaps = [
            min(end, g.planned_end_ms) - max(start, g.planned_start_ms) for g in gold.scenes
        ]
        best = max(range(len(gold.scenes)), key=lambda i: (overlaps[i], -i))
        mapping[scene.scene_key] = gold.scenes[best].scene_key
    return mapping


def project(entity_key: str | None, entity_map: Mapping[str, str]) -> str | None:
    """모델 객체 키를 골드 키로 옮긴다. 골드에 없는 객체는 '?키' 로 남겨 어떤 골드 조건과도 맞지 않게 한다."""
    if entity_key is None:
        return None
    return entity_map.get(entity_key, f"?{entity_key}")
