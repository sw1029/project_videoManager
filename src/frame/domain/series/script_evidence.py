"""대본을 테마의 근거로 쓴다 (ADR-0028). 순수 함수: 테마 + 뽑은 대본 + 화면에서 읽은 문구 -> 근거와 어긋남.

프레임에서 읽은 것(화면 문구·변수 값)과 소리에서 뽑은 것(대본)을 서로 견준다.
- 변수 값: 테마가 적은 원본의 값을 화면에서 봤는가, 말로도 했는가. 같은 단위의 다른 값을 말했으면 어긋남이다.
  수는 값으로 견준다 (화면 '4,500원' = 말 '사천오백 원').
- 화면 문구: 그 시간에 한 말을 받아 적은 자막인가(SPOKEN), 말에 나온 문구인가(PARTLY), 말하지 않은 문구인가.
- 어긋남: 조용히 한쪽을 고르지 않는다. 화면과 말의 값이 다르거나(VALUE) 자막과 말이 조금 다르면(WORDING)
  목록에 남긴다. 화면을 잘못 읽었는지 말을 잘못 받아 적었는지는 사람이 정한다.
- 내레이션 규칙: 자리마다 말한 글·글자 수·말한 시간·시작 여유. 화면 속 대사(DIALOGUE)는 넣지 않는다
  (녹음을 붙여 다시 만들 수 없다).
- 자막 규칙: 말을 받아 적은 자막이 있었으면 자막 규칙에 표시한다 (TextRule.follows_speech).
  그 테마로 만든 편에서는 그 자리의 대본 줄이 곧 자막이 된다.

여기서 모델을 부르지 않는다. 대본의 줄을 고친 뒤에도 같은 함수로 다시 견주면 된다 (다시 과금하지 않는다).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any

from frame.domain.screen import TextRole
from frame.domain.script import (
    LineKind,
    SpokenLine,
    SpokenScript,
    canonical_text,
    mentions,
    quantities,
    quantity_conflicts,
)
from frame.domain.series.source_analysis import ScreenText
from frame.domain.series.theme import (
    NarrationBeat,
    NarrationSpec,
    Theme,
    VariableScope,
    template_variables,
)

WINDOW_MS = 500  # 화면 문구가 보인 시간의 앞뒤로 이만큼까지의 말을 그 문구의 말로 본다
SPOKEN_ABOVE = 0.8  # 글자 유사도가 이 이상이면 말을 받아 적은 자막이다
PARTLY_ABOVE = 0.5  # 이 이상이면 말에 나온 문구다
WORDING_BELOW = 0.9  # 자막 같은데(0.6 이상) 이보다 낮으면 '조금 다르다' 고 알린다
WORDING_ABOVE = 0.6


class Agreement(StrEnum):
    AGREE = "AGREE"  # 화면의 값을 말로도 했다
    CONFLICT = "CONFLICT"  # 같은 단위의 다른 값을 말했다
    SCREEN_ONLY = "SCREEN_ONLY"  # 화면에만 있다
    SPEECH_ONLY = "SPEECH_ONLY"  # 말에만 있다
    UNSEEN = "UNSEEN"  # 화면에서도 말에서도 찾지 못했다


class TextRelation(StrEnum):
    SPOKEN = "SPOKEN"  # 그 시간에 한 말을 받아 적은 자막
    PARTLY = "PARTLY"  # 말에 나온 문구 (말의 일부이거나 같은 값을 담았다)
    UNSPOKEN = "UNSPOKEN"  # 말하지 않은 문구


class DiscrepancyKind(StrEnum):
    VALUE = "VALUE"  # 화면과 말의 값이 다르다 (같은 단위)
    WORDING = "WORDING"  # 자막으로 보이는 문구가 말과 조금 다르다


@dataclass(frozen=True)
class VariableEvidence:
    key: str
    value: str  # 테마가 적은 원본의 값
    agreement: Agreement
    line_ids: tuple[str, ...] = ()  # 그 값을 말한 줄 (CONFLICT 면 다른 값을 말한 줄)
    said: str = ""  # CONFLICT 일 때 말한 값

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "agreement": str(self.agreement),
            "line_ids": list(self.line_ids),
            "said": self.said,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VariableEvidence:
        return cls(
            str(data["key"]),
            str(data.get("value", "")),
            Agreement(data["agreement"]),
            tuple(str(i) for i in data.get("line_ids", ())),
            str(data.get("said", "")),
        )


@dataclass(frozen=True)
class TextEvidence:
    text: str
    slot_key: str | None
    relation: TextRelation
    line_ids: tuple[str, ...] = ()
    similarity: float = 0.0
    role: TextRole = TextRole.OTHER

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "slot_key": self.slot_key,
            "relation": str(self.relation),
            "line_ids": list(self.line_ids),
            "similarity": self.similarity,
            "role": str(self.role),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TextEvidence:
        return cls(
            str(data["text"]),
            data.get("slot_key"),
            TextRelation(data["relation"]),
            tuple(str(i) for i in data.get("line_ids", ())),
            float(data.get("similarity", 0.0)),
            TextRole(data.get("role", TextRole.OTHER)),
        )


@dataclass(frozen=True)
class Discrepancy:
    kind: DiscrepancyKind
    slot_key: str | None
    line_ids: tuple[str, ...]
    shown: str  # 화면에서 읽은 것
    said: str  # 말한 것
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "slot_key": self.slot_key,
            "line_ids": list(self.line_ids),
            "shown": self.shown,
            "said": self.said,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Discrepancy:
        return cls(
            DiscrepancyKind(data["kind"]),
            data.get("slot_key"),
            tuple(str(i) for i in data.get("line_ids", ())),
            str(data.get("shown", "")),
            str(data.get("said", "")),
            str(data.get("detail", "")),
        )


@dataclass(frozen=True)
class UnlinkedQuantity:
    """말에는 나왔지만 화면 문구에도 변수에도 없는 수량. 빠뜨린 변수일 수 있다."""

    line_id: str
    text: str  # '3000원' 꼴

    def to_dict(self) -> dict[str, str]:
        return {"line_id": self.line_id, "text": self.text}


@dataclass(frozen=True)
class ScriptEvidence:
    variables: tuple[VariableEvidence, ...] = ()
    texts: tuple[TextEvidence, ...] = ()
    discrepancies: tuple[Discrepancy, ...] = ()
    unlinked: tuple[UnlinkedQuantity, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "variables": [v.to_dict() for v in self.variables],
            "texts": [t.to_dict() for t in self.texts],
            "discrepancies": [d.to_dict() for d in self.discrepancies],
            "unlinked": [u.to_dict() for u in self.unlinked],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> ScriptEvidence:
        if not data:
            return cls()
        return cls(
            tuple(VariableEvidence.from_dict(v) for v in data.get("variables", ())),
            tuple(TextEvidence.from_dict(t) for t in data.get("texts", ())),
            tuple(Discrepancy.from_dict(d) for d in data.get("discrepancies", ())),
            tuple(
                UnlinkedQuantity(str(u["line_id"]), str(u["text"]))
                for u in data.get("unlinked", ())
            ),
        )


def link_script(
    theme: Theme, script: SpokenScript, screen_texts: Sequence[ScreenText]
) -> ScriptEvidence:
    """테마의 변수 값과 화면 문구를 대본과 견준다."""
    texts: list[TextEvidence] = []
    discrepancies: list[Discrepancy] = []
    for shown in screen_texts:
        evidence, found = _text_evidence(theme, script, shown)
        texts.append(evidence)
        discrepancies += [d for d in found if d not in discrepancies]
    variables = tuple(
        _variable_evidence(theme, script, screen_texts, variable.key, variable.example)
        for variable in theme.variables
        if variable.scope is VariableScope.EPISODE and variable.example.strip()
    )
    for variable in variables:
        covered = any(
            d.kind is DiscrepancyKind.VALUE and set(d.line_ids) & set(variable.line_ids)
            for d in discrepancies
        )
        if variable.agreement is Agreement.CONFLICT and not covered:
            line = script.line(variable.line_ids[0])
            discrepancies.append(
                Discrepancy(
                    DiscrepancyKind.VALUE,
                    line.scene_key,
                    variable.line_ids,
                    shown=variable.value,
                    said=line.text,
                    detail=f"변수 '{variable.key}': 테마 {variable.value} / 말 {variable.said}",
                )
            )
    return ScriptEvidence(
        variables, tuple(texts), tuple(discrepancies), _unlinked(theme, script, screen_texts)
    )


def narration_from_script(theme: Theme, script: SpokenScript) -> NarrationSpec:
    """대본에서 잰 내레이션 규칙: 자리마다 말한 글·글자 수·말한 시간·시작 여유, 전체의 빠르기.

    자리의 구실(purpose)과 말투(tone)는 소리로 재는 것이 아니라 테마에 있던 것을 그대로 둔다.
    """
    spoken = [line for line in script.lines if line.kind is not LineKind.DIALOGUE]
    if not spoken:
        return NarrationSpec(present=False)
    starts: dict[str, int] = {}
    cursor = 0
    for slot in theme.format.slots:
        starts[slot.slot_key] = cursor
        cursor += slot.duration_ms
    beats: list[NarrationBeat] = []
    for slot in theme.format.slots:
        lines = [line for line in spoken if line.scene_key == slot.slot_key]
        if not lines:
            continue
        known = theme.narration.beat(slot.slot_key)
        beats.append(
            NarrationBeat(
                slot.slot_key,
                purpose=known.purpose if known is not None else "",
                example=" ".join(line.text for line in lines),
                chars=sum(line.chars for line in lines),
                spoken_ms=sum(line.duration_ms for line in lines),
                lead_in_ms=max(0, lines[0].start_ms - starts[slot.slot_key]),
                line_ids=tuple(line.line_id for line in lines),
            )
        )
    chars = sum(line.chars for line in spoken)
    spoken_ms = sum(line.duration_ms for line in spoken)
    return NarrationSpec(
        present=True,
        language=script.language or theme.narration.language,
        tone=theme.narration.tone,
        pace_cps=chars / (spoken_ms / 1000) if spoken_ms > 0 else None,
        beats=tuple(beats),
    )


def apply_script(theme: Theme, script: SpokenScript, evidence: ScriptEvidence) -> Theme:
    """대본에서 얻은 것을 테마에 옮긴다: 내레이션 규칙, 말을 따르는 자막 규칙.

    변수 값과 문구 틀은 바꾸지 않는다. 화면과 말이 다르면 어긋남으로 남길 뿐이다.
    """
    captions = [t for t in evidence.texts if t.role is TextRole.CAPTION]
    written_down = [t for t in captions if t.relation is TextRelation.SPOKEN]
    follows = bool(written_down) and len(written_down) * 2 >= len(captions)
    rules = tuple(
        replace(rule, follows_speech=follows and rule.role is TextRole.CAPTION)
        for rule in theme.text_rules
    )
    return replace(theme, narration=narration_from_script(theme, script), text_rules=rules)


# ---- 안쪽 ----------------------------------------------------------------------
def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0


def _slot_at(theme: Theme, at_ms: float) -> str | None:
    cursor = 0
    for slot in theme.format.slots:
        if cursor <= at_ms < cursor + slot.duration_ms:
            return slot.slot_key
        cursor += slot.duration_ms
    return None


def _text_evidence(
    theme: Theme, script: SpokenScript, shown: ScreenText
) -> tuple[TextEvidence, list[Discrepancy]]:
    slot_key = _slot_at(theme, (shown.start_ms + shown.end_ms) / 2)
    nearby = [
        line
        for line in script.lines
        if line.start_ms < shown.end_ms + WINDOW_MS and line.end_ms > shown.start_ms - WINDOW_MS
    ]
    wanted = canonical_text(shown.text)
    discrepancies: list[Discrepancy] = []
    best: tuple[float, bool, tuple[str, ...]] = (0.0, False, ())
    candidates: list[tuple[tuple[SpokenLine, ...], str]] = [
        ((line,), canonical_text(line.text)) for line in nearby
    ]
    if len(nearby) > 1:
        candidates.append((tuple(nearby), "".join(canonical_text(line.text) for line in nearby)))
    for lines, said in candidates:
        if not wanted or not said:
            continue
        contained = wanted in said or said in wanted
        shares = any(_shares_quantity(shown.text, line.text) for line in lines)
        score = (_ratio(wanted, said), contained or shares, tuple(line.line_id for line in lines))
        if (score[0], score[1]) > (best[0], best[1]):
            best = score
    for line in nearby:
        for conflict in quantity_conflicts(shown.text, line.text):
            left = ", ".join(format(v.normalize(), "f") for v in conflict.left)
            right = ", ".join(format(v.normalize(), "f") for v in conflict.right)
            discrepancies.append(
                Discrepancy(
                    DiscrepancyKind.VALUE,
                    slot_key,
                    (line.line_id,),
                    shown=shown.text,
                    said=line.text,
                    detail=f"{conflict.unit}: 화면 {left} / 말 {right}",
                )
            )
    similarity, related, line_ids = best
    if similarity >= SPOKEN_ABOVE:
        relation = TextRelation.SPOKEN
    elif similarity >= PARTLY_ABOVE or related:
        relation = TextRelation.PARTLY
    else:
        relation, line_ids = TextRelation.UNSPOKEN, ()
    near_miss = WORDING_ABOVE <= similarity < WORDING_BELOW and not related
    if near_miss and not discrepancies:
        said_text = " ".join(script.line(line_id).text for line_id in line_ids)
        discrepancies.append(
            Discrepancy(
                DiscrepancyKind.WORDING,
                slot_key,
                line_ids,
                shown=shown.text,
                said=said_text,
                detail=f"글자 유사도 {similarity:.2f}",
            )
        )
    evidence = TextEvidence(
        shown.text, slot_key, relation, line_ids, round(similarity, 4), shown.role
    )
    return evidence, discrepancies


def _shares_quantity(a: str, b: str) -> bool:
    there = {(q.value, q.unit) for q in quantities(b)}
    return any((q.value, q.unit) in there for q in quantities(a))


def _variable_slots(theme: Theme, key: str) -> set[str]:
    """그 변수를 쓰는 문구 규칙이 나오는 자리. 규칙이 없거나 모든 자리이면 빈 집합 (어느 자리든)."""
    slots: set[str] = set()
    for rule in theme.text_rules:
        if key in template_variables(rule.template):
            if not rule.slot_keys:
                return set()
            slots |= set(rule.slot_keys)
    return slots


def _variable_evidence(
    theme: Theme, script: SpokenScript, screen_texts: Sequence[ScreenText], key: str, value: str
) -> VariableEvidence:
    shown = any(mentions(text.text, value) for text in screen_texts)
    said = tuple(line.line_id for line in script.lines if mentions(line.text, value))
    if said:
        return VariableEvidence(
            key, value, Agreement.AGREE if shown else Agreement.SPEECH_ONLY, said
        )
    slots = _variable_slots(theme, key)
    conflicting: list[SpokenLine] = []
    other_values: list[str] = []
    for line in script.lines:
        if slots and line.scene_key not in slots:
            continue
        for conflict in quantity_conflicts(value, line.text):
            conflicting.append(line)
            other_values += [f"{format(v.normalize(), 'f')}{conflict.unit}" for v in conflict.right]
    if conflicting:
        return VariableEvidence(
            key,
            value,
            Agreement.CONFLICT,
            tuple(dict.fromkeys(line.line_id for line in conflicting)),
            ", ".join(dict.fromkeys(other_values)),
        )
    return VariableEvidence(key, value, Agreement.SCREEN_ONLY if shown else Agreement.UNSEEN)


def _unlinked(
    theme: Theme, script: SpokenScript, screen_texts: Sequence[ScreenText]
) -> tuple[UnlinkedQuantity, ...]:
    known = {(q.value, q.unit) for text in screen_texts for q in quantities(text.text)}
    known |= {
        (q.value, q.unit) for variable in theme.variables for q in quantities(variable.example)
    }
    found: list[UnlinkedQuantity] = []
    for line in script.lines:
        for quantity in quantities(line.text):
            item = UnlinkedQuantity(line.line_id, quantity.text)
            if (quantity.value, quantity.unit) not in known and item not in found:
                found.append(item)
    return tuple(found)
