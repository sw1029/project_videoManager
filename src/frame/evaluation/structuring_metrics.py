"""구조화 골드셋 지표 (US-06 AC-11, T2·T6). 순수 함수.

모델이 낸 IR 을 기대 IR(골드)과 견준다. 키 이름은 모델·프롬프트마다 달라지므로(`red_mug` / `cup`, `scene_1` / `smile`)
객체는 종류와 설명(골드의 별칭)으로, 장면은 시간 겹침으로 먼저 맞추고, 요구조건은 골드 좌표로 옮겨 비교한다.

지표
- 객체 재현율·정밀도: 필수 골드 객체 중 맞춘 비율 / 모델 객체 중 골드에 있는 비율
- 요구조건 정밀도·재현율·F1: (장면, 객체, 술어, 상대 객체) 가 같고 값이 정규화 뒤 일치하면 맞춤.
  장면을 쪼개 같은 조건을 되풀이한 것은 하나로 센다 (쪼갠 것은 장면 수 지표가 잡는다)
- 장면 수 일치·경계 오차: 장면 수가 같은지, 같으면 장면 경계의 평균 |Δms|. 전체 길이 오차도 낸다
- 등장 장면(보조): (장면, 객체) 쌍의 F1. 수정 전파 범위가 여기서 정해진다

골드 주석 (기대 IR 에 덧붙이며 IR.from_dict 는 읽지 않는다)
- 객체: aliases(키·설명에서 찾을 이름), accept_kinds(같다고 볼 다른 종류), optional
- 장면: optional_entities(그 장면에 있어도 없어도 되는 객체)
- 요구조건: accept(다른 값), accept_predicates(다른 술어), optional
선택 항목은 맞혀도 세지 않고(중립), 값이 틀리면 오탐으로 센다. 선택 객체가 걸린 요구조건은 저절로 선택이 된다.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from frame.domain.enums import EntityKind
from frame.domain.errors import ValidationError
from frame.domain.ir import EntitySpec, IntermediateRepresentation, RequirementSpec
from frame.domain.vocabulary import DEFAULT_VOCABULARY, ValueKind

ENTITY_RECALL_TARGET = 0.9
REQUIREMENT_F1_TARGET = 0.8
SCENE_COUNT_MATCH_TARGET = 0.8

_LOWER_IS_BETTER = frozenset({"mean_boundary_error_ms", "mean_duration_error_ms"})
_EPSILON = 1e-9


@dataclass(frozen=True)
class Tally:
    """맞춤(tp)·오탐(fp)·놓침(fn) 개수. 분모가 0 인 비율은 0.0 이다."""

    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return _ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float:
        return _ratio(self.tp, self.tp + self.fn)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def __add__(self, other: Tally) -> Tally:
        return Tally(self.tp + other.tp, self.fp + other.fp, self.fn + other.fn)


# ---- 골드 -------------------------------------------------------------------
@dataclass(frozen=True)
class GoldEntity:
    entity_key: str
    kind: EntityKind
    aliases: tuple[str, ...]
    accept_kinds: frozenset[EntityKind] = frozenset()
    optional: bool = False

    def accepts(self, kind: EntityKind) -> bool:
        return kind is self.kind or kind in self.accept_kinds


@dataclass(frozen=True)
class GoldRequirement:
    spec: RequirementSpec
    accept: tuple[str, ...] = ()
    accept_predicates: frozenset[str] = frozenset()
    optional: bool = False

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def context(self) -> tuple[str, str | None, str | None]:
        return (self.spec.scene_key, self.spec.entity_key, self.spec.related_entity_key)

    def matches(self, value: str) -> bool:
        expected = (self.spec.expected_value, *self.accept)
        return any(values_match(self.spec.predicate, e, value) for e in expected)


@dataclass(frozen=True)
class GoldSpec:
    ir: IntermediateRepresentation
    entities: tuple[GoldEntity, ...]
    requirements: tuple[GoldRequirement, ...]
    optional_appearances: frozenset[tuple[str, str]]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> GoldSpec:
        """주석이 붙은 기대 IR 문서를 읽는다. 요구조건의 scene_key 는 생략할 수 있다."""
        doc = copy.deepcopy(dict(document))
        raw_scenes = doc.get("scenes", [])
        for raw in raw_scenes:
            for req in raw.get("requirements", []):
                req.setdefault("scene_key", raw.get("scene_key"))
        ir = IntermediateRepresentation.from_dict(doc)
        ir.validate()

        entities = tuple(
            _gold_entity(raw, spec)
            for raw, spec in zip(doc.get("entities", []), ir.entities, strict=True)
        )
        optional_keys = {e.entity_key for e in entities if e.optional}
        optional_pairs: set[tuple[str, str]] = set()
        requirements: list[GoldRequirement] = []
        for raw, scene in zip(raw_scenes, ir.scenes, strict=True):
            extra = set(raw.get("optional_entities", ()))
            unknown = extra - set(scene.entity_keys)
            if unknown:
                raise ValidationError(
                    f"장면 '{scene.scene_key}' 의 optional_entities 가 장면에 없는 객체를 가리킵니다: "
                    f"{sorted(unknown)}"
                )
            optional_pairs.update(
                (scene.scene_key, key)
                for key in scene.entity_keys
                if key in optional_keys or key in extra
            )
            for raw_req, spec in zip(raw.get("requirements", []), scene.requirements, strict=True):
                refs = {
                    (scene.scene_key, k) for k in (spec.entity_key, spec.related_entity_key) if k
                }
                requirements.append(
                    GoldRequirement(
                        spec,
                        accept=tuple(str(v) for v in raw_req.get("accept", ())),
                        accept_predicates=frozenset(
                            DEFAULT_VOCABULARY.normalize(p)
                            for p in raw_req.get("accept_predicates", ())
                        ),
                        optional=bool(raw_req.get("optional")) or bool(refs & optional_pairs),
                    )
                )
        return cls(ir, entities, tuple(requirements), frozenset(optional_pairs))

    @property
    def required_entity_keys(self) -> tuple[str, ...]:
        return tuple(e.entity_key for e in self.entities if not e.optional)

    @property
    def required_appearances(self) -> frozenset[tuple[str, str]]:
        pairs = {(s.scene_key, key) for s in self.ir.scenes for key in s.entity_keys}
        return frozenset(pairs - self.optional_appearances)


def _gold_entity(raw: Mapping[str, Any], spec: EntitySpec) -> GoldEntity:
    aliases = tuple(str(a) for a in raw.get("aliases", ()) if str(a).strip())
    return GoldEntity(
        spec.entity_key,
        spec.kind,
        aliases or (spec.entity_key.replace("_", " "), spec.description),
        frozenset(EntityKind(k) for k in raw.get("accept_kinds", ())),
        bool(raw.get("optional")),
    )


# ---- 사례 점수 ---------------------------------------------------------------
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
    mismatched_requirements: tuple[str, ...] = ()  # 냈지만 값이 틀린 조건
    spurious_requirements: tuple[str, ...] = ()  # 골드에 없는 조건
    error: str | None = None  # 구조화 실패

    @property
    def scene_count_match(self) -> bool:
        return self.error is None and self.expected_scene_count == self.predicted_scene_count


def score_case(case_id: str, gold: GoldSpec, predicted: IntermediateRepresentation) -> CaseScore:
    entity_map = _match_entities(gold.entities, predicted.entities)  # 모델 키 → 골드 키
    scene_map = _map_scenes(gold.ir, predicted)  # 모델 장면 키 → 골드 장면 키
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


# ---- 객체 맞추기 --------------------------------------------------------------
def _match_entities(gold: Sequence[GoldEntity], predicted: Sequence[EntitySpec]) -> dict[str, str]:
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
    words = _words(f"{entity.entity_key.replace('_', ' ')} {entity.description}")
    stems = {_stem(w) for w in words}
    return sum(1 for alias in aliases if _alias_found(_words(alias), words, stems))


def _alias_found(alias: list[str], words: list[str], stems: set[str]) -> bool:
    if not alias:
        return False
    if all(w.isascii() for w in alias):
        return {_stem(w) for w in alias} <= stems
    if len(alias) == 1:
        # 한국어 복합어는 머리말이 뒤에 온다 (머그컵 → 컵). 앞부분만 같은 말(컵받침)은 다른 물건이다
        return any(w.endswith(alias[0]) for w in words)
    return f" {' '.join(alias)} " in f" {' '.join(words)} "


# ---- 장면 맞추기 --------------------------------------------------------------
def _map_scenes(
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


# ---- 요구조건 -----------------------------------------------------------------
def _project(entity_key: str | None, entity_map: Mapping[str, str]) -> str | None:
    """모델 객체 키를 골드 키로 옮긴다. 골드에 없는 객체는 '?키' 로 남겨 어떤 골드 조건과도 맞지 않게 한다."""
    if entity_key is None:
        return None
    return entity_map.get(entity_key, f"?{entity_key}")


def _score_requirements(
    gold: GoldSpec,
    predicted: IntermediateRepresentation,
    scene_map: Mapping[str, str],
    entity_map: Mapping[str, str],
) -> tuple[Tally, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    by_context: dict[tuple[str, str | None, str | None], list[GoldRequirement]] = {}
    for requirement in gold.requirements:
        by_context.setdefault(requirement.context, []).append(requirement)

    answers: dict[str, list[RequirementSpec]] = {}  # 골드 조건 키 → 그 조건을 겨냥한 모델 조건
    spurious: dict[tuple[Any, ...], str] = {}  # 골드 좌표의 자리 → 처음 본 모델 조건 키
    for req in predicted.iter_requirements():
        context = (
            scene_map.get(req.scene_key),
            _project(req.entity_key, entity_map),
            _project(req.related_entity_key, entity_map),
        )
        target = _target(by_context.get(context, ()), req)
        if target is None:
            spurious.setdefault((*context, req.predicate), req.key)
        else:
            answers.setdefault(target.key, []).append(req)

    tp = fp = fn = 0
    missed: list[str] = []
    mismatched: list[str] = []
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
                f"{requirement.key}: 기대 '{requirement.spec.expected_value}', "
                f"결과 '{given[0].expected_value}'"
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
        (scene_map[s.scene_key], _project(key, entity_map))
        for s in predicted.scenes
        if s.scene_key in scene_map
        for key in s.entity_keys
    }
    return Tally(tp=len(actual & required), fp=len(actual - expected), fn=len(required - actual))


# ---- 값 비교 -------------------------------------------------------------------
_WORD = re.compile(r"[^\W_]+")
_NUMERIC = re.compile(r"[+-]?\d+(?:\.\d+)?")
_LEADING_DIGITS = re.compile(r"\d+")
_QUOTES = "'\"‘’“”「」『』`"
_STOPWORDS = frozenset(
    "a an the and or of with to at in on into onto for from by while its his her their is are s".split()
)  # s: 소유격(bird's)이 쪼개져 남은 것
_VOWELS = frozenset("aeiouy")
# (어미, 그 어미를 걷어낼 최소 낱말 길이). 앞에서부터 하나만 걷어낸다
_SUFFIXES = (("ing", 6), ("ied", 5), ("ies", 5), ("es", 5), ("ed", 5), ("ly", 6), ("s", 4))
_COLOR_WORDS = {
    "grey": "gray",
    "빨간": "red", "빨강": "red", "빨간색": "red", "빨강색": "red", "붉은": "red",
    "주황": "orange", "주황색": "orange",
    "노란": "yellow", "노랑": "yellow", "노란색": "yellow", "노랑색": "yellow",
    "초록": "green", "초록색": "green", "녹색": "green",
    "청록": "teal", "청록색": "teal",
    "파란": "blue", "파랑": "blue", "파란색": "blue", "파랑색": "blue",
    "남색": "navy",
    "보라": "purple", "보라색": "purple",
    "분홍": "pink", "분홍색": "pink", "핑크": "pink", "핑크색": "pink",
    "갈색": "brown",
    "흰": "white", "하얀": "white", "흰색": "white", "하얀색": "white",
    "검은": "black", "검정": "black", "검은색": "black", "검정색": "black",
    "회색": "gray",
    "은색": "silver",
    "금색": "gold",
}  # fmt: skip
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "하나": "1", "한": "1", "둘": "2", "두": "2", "셋": "3", "세": "3", "넷": "4", "네": "4",
    "다섯": "5", "여섯": "6", "일곱": "7", "여덟": "8", "아홉": "9", "열": "10",
}  # fmt: skip


def values_match(predicate: str, expected: str, actual: str) -> bool:
    """골드 값과 모델 값이 같은 조건을 뜻하는가. 어휘의 값 정규화를 거친 뒤 값 종류별로 견준다.

    - ENTITY(관계): 상대 객체를 맞춘 것으로 비교가 끝났다
    - LITERAL(문구·브랜드): 따옴표·공백만 정리하고 철자·대소문자까지 같아야 한다
    - NUMBER: 숫자로 (three / 세 마리 → 3)
    - 그 밖(COLOR·TEXT·POSITION): 골드 값의 내용어가 모델 값에 모두 있으면 맞춤 (warm ⊂ warm cafe lighting).
      영어 어미(smiling/smiles → smile), 한국어 색 이름(빨간색 → red), 붙여 쓰기(close up / closeup)를 맞춘다
    """
    predicate = DEFAULT_VOCABULARY.normalize(predicate)
    kind = DEFAULT_VOCABULARY.value_kind(predicate)
    if kind is ValueKind.ENTITY:
        return True
    expected = DEFAULT_VOCABULARY.normalize_value(predicate, expected)
    actual = DEFAULT_VOCABULARY.normalize_value(predicate, actual)
    if kind is ValueKind.LITERAL:
        return _literal(expected) == _literal(actual)
    if kind is ValueKind.NUMBER:
        return _number(expected) == _number(actual)
    return _phrase_match(expected, actual)


def _phrase_match(expected: str, actual: str) -> bool:
    """골드 값의 내용어가 모델 값에 모두 있는가. 띄어 쓴 말과 붙여 쓴 말(close up / closeup)은 같다고 본다."""
    wanted = [w for w in _words(expected) if w not in _STOPWORDS]
    words = _words(actual)
    if not wanted:
        return "".join(_words(expected)) == "".join(words)
    stems = {_stem(w) for w in words if w not in _STOPWORDS}
    joined = {
        "".join(words[i:j])
        for i in range(len(words))
        for j in range(i + 1, min(i + 4, len(words) + 1))
    }  # 모델 값의 이웃한 1~3 낱말을 붙인 것
    i = 0
    while i < len(wanted):
        if _stem(wanted[i]) in stems or wanted[i] in joined:
            i += 1
            continue
        size = next(
            (n for n in (2, 3) if i + n <= len(wanted) and "".join(wanted[i : i + n]) in joined),
            0,
        )
        if not size:
            return False
        i += size
    return True


def _words(text: str) -> list[str]:
    return [_COLOR_WORDS.get(w, w) for w in _WORD.findall(text.lower())]


def _stem(word: str) -> str:
    """영어 낱말의 굴절 어미를 걷어낸다 (smiling/smiles/smile → smil, rainy → rain). 한국어·숫자는 그대로."""
    if not (word.isascii() and word.isalpha()):
        return word
    for suffix, min_length in _SUFFIXES:
        if len(word) >= min_length and word.endswith(suffix) and not word.endswith("ss"):
            stem = word[: -len(suffix)]
            if _VOWELS & set(stem):
                word = stem
                break
    if len(word) > 3 and word.endswith("y"):
        word = word[:-1]
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in _VOWELS:
        word = word[:-1]
    return word


def _literal(value: str) -> str:
    return " ".join(value.strip().strip(_QUOTES).split())


def _number(value: str) -> str:
    if _NUMERIC.fullmatch(value):
        return value
    words = _words(value)
    if not words:
        return ""
    first = _NUMBER_WORDS.get(words[0], words[0])
    digits = _LEADING_DIGITS.match(first)
    return digits.group(0) if digits else first


# ---- 집계·비교 ------------------------------------------------------------------
@dataclass(frozen=True)
class StructuringEvaluation:
    """여러 사례를 합친 결과. 비율은 사례의 개수를 모두 더해 계산한다 (micro 평균)."""

    cases: int
    failures: int
    entities: Tally
    requirements: Tally
    appearances: Tally
    scene_count_matches: int
    mean_boundary_error_ms: float | None
    mean_duration_error_ms: float | None
    entity_recall_target: float = ENTITY_RECALL_TARGET
    requirement_f1_target: float = REQUIREMENT_F1_TARGET
    scene_count_match_target: float = SCENE_COUNT_MATCH_TARGET

    @property
    def entity_recall(self) -> float:
        return self.entities.recall

    @property
    def entity_precision(self) -> float:
        return self.entities.precision

    @property
    def requirement_precision(self) -> float:
        return self.requirements.precision

    @property
    def requirement_recall(self) -> float:
        return self.requirements.recall

    @property
    def requirement_f1(self) -> float:
        return self.requirements.f1

    @property
    def scene_count_match_rate(self) -> float:
        return _ratio(self.scene_count_matches, self.cases)

    @property
    def meets_target(self) -> bool:
        return (
            self.cases > 0
            and self.entity_recall >= self.entity_recall_target
            and self.requirement_f1 >= self.requirement_f1_target
            and self.scene_count_match_rate >= self.scene_count_match_target
        )


def summarize(scores: Iterable[CaseScore]) -> StructuringEvaluation:
    scores = list(scores)
    boundaries = [s.boundary_error_ms for s in scores if s.boundary_error_ms is not None]
    durations = [s.duration_error_ms for s in scores if s.duration_error_ms is not None]
    return StructuringEvaluation(
        cases=len(scores),
        failures=sum(1 for s in scores if s.error is not None),
        entities=sum((s.entities for s in scores), Tally()),
        requirements=sum((s.requirements for s in scores), Tally()),
        appearances=sum((s.appearances for s in scores), Tally()),
        scene_count_matches=sum(1 for s in scores if s.scene_count_match),
        mean_boundary_error_ms=_mean(boundaries),
        mean_duration_error_ms=_mean(durations),
    )


def headline(evaluation: StructuringEvaluation) -> dict[str, float | None]:
    """기록·비교에 쓰는 대표 지표 (순서 고정)."""
    return {
        "entity_recall": evaluation.entity_recall,
        "entity_precision": evaluation.entity_precision,
        "requirement_precision": evaluation.requirement_precision,
        "requirement_recall": evaluation.requirement_recall,
        "requirement_f1": evaluation.requirement_f1,
        "appearance_f1": evaluation.appearances.f1,
        "scene_count_match_rate": evaluation.scene_count_match_rate,
        "mean_boundary_error_ms": evaluation.mean_boundary_error_ms,
        "mean_duration_error_ms": evaluation.mean_duration_error_ms,
    }


@dataclass(frozen=True)
class MetricChange:
    name: str
    baseline: float | None
    candidate: float | None

    @property
    def lower_is_better(self) -> bool:
        return self.name in _LOWER_IS_BETTER

    @property
    def delta(self) -> float | None:
        if self.baseline is None or self.candidate is None:
            return None
        return self.candidate - self.baseline

    @property
    def regressed(self) -> bool:
        delta = self.delta
        if delta is None:
            return False
        return delta > _EPSILON if self.lower_is_better else delta < -_EPSILON


def compare_headlines(
    baseline: Mapping[str, float | None], candidate: Mapping[str, float | None]
) -> tuple[MetricChange, ...]:
    """두 결과의 대표 지표를 견준다 (기준의 순서, 한쪽에만 있는 지표는 변화량 None)."""
    names = [*baseline, *(n for n in candidate if n not in baseline)]
    return tuple(MetricChange(n, baseline.get(n), candidate.get(n)) for n in names)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None
