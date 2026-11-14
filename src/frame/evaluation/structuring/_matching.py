"""짝 맞추기: 모델 객체를 골드 객체에, 모델 장면을 골드 장면에 붙인다.

키 이름은 모델·프롬프트마다 달라지므로(`red_mug` / `cup`, `scene_1` / `smile`) 객체는 종류와 설명(골드의 별칭)으로,
장면은 시간 겹침으로 맞춘다. 채점(_scoring)은 이 대응으로 모델의 요구조건을 골드 좌표로 옮긴다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from frame.domain.ir import EntitySpec, IntermediateRepresentation

from ._gold import GoldEntity
from ._values import stem, words

# 모두 훑을 대응 수의 상한 (골드 객체마다 '후보 수 + 1(짝 없음)' 을 곱한 값).
# 골드 15건과 첫 실제 실행 응답에서는 가장 커도 수백이다
SEARCH_LIMIT = 50_000
EXHAUSTIVE = "exhaustive"  # 모든 대응을 훑어 가장 좋은 것을 골랐다
GREEDY = "greedy"  # 상한을 넘어 차례로 골랐다 (가장 좋은 대응을 놓쳤을 수 있다)


def match_entities(
    gold: Sequence[GoldEntity], predicted: Sequence[EntitySpec]
) -> tuple[dict[str, str], str]:
    """(모델 객체 키 → 골드 객체 키 (일대일), 고른 방법 EXHAUSTIVE | GREEDY).

    종류가 같고 별칭이 하나라도 키·설명에 있는 짝만 후보다. 대응 수가 SEARCH_LIMIT 이하면 모두 훑어
    (필수 짝 수, 선택 짝 수, 맞은 별칭 수) 가 가장 큰 대응을 고른다. 넘으면(객체·후보가 아주 많을 때) 필수 객체부터
    별칭이 가장 많이 맞는 빈 짝을 차례로 고른다. 그래서 채점이 멈춘 것처럼 보이지 않는다. 다만 가장 좋은 대응을
    놓칠 수 있으므로 GREEDY 를 돌려주어 채점 결과·기록에 남긴다.
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
    if search_space(candidates) > SEARCH_LIMIT:
        chosen = _pick_in_order(order, candidates)
        return {predicted[j].entity_key: gold[i].entity_key for i, j in chosen.items()}, GREEDY
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
    return {predicted[j].entity_key: gold[i].entity_key for i, j in best.items()}, EXHAUSTIVE


def search_space(candidates: Sequence[Sequence[tuple[int, int]]]) -> int:
    """모두 훑을 때의 대응 수 (골드 객체마다 후보 수 + 1 을 곱한다). 상한을 넘으면 거기서 멈춰 센다."""
    size = 1
    for options in candidates:
        size *= len(options) + 1
        if size > SEARCH_LIMIT:
            break
    return size


def _pick_in_order(
    order: Sequence[int], candidates: Sequence[Sequence[tuple[int, int]]]
) -> dict[int, int]:
    """골드 객체를 order 순서로 돌며 별칭이 가장 많이 맞는 빈 짝을 고른다 (탐욕)."""
    used: set[int] = set()
    chosen: dict[int, int] = {}
    for i in order:
        for _hits, j in candidates[i]:
            if j not in used:
                chosen[i] = j
                used.add(j)
                break
    return chosen


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
