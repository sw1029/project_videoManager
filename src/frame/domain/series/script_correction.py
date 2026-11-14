"""뽑은 대본의 줄을 고친 뒤, 그 줄에 걸린 것만 다시 맞춘다 (ADR-0029). 순수 함수.

인식기가 잘못 받아 적은 줄을 사람이 고치면 (SpokenLine.with_text), 그 줄을 근거로 삼았던 것만 다시 맞춘다.
전체를 다시 뽑지 않는다. 모델도 인식기도 부르지 않는다.
- 내레이션 규칙: 고친 줄이 있는 자리의 것만 다시 잰다 (전체의 빠르기도 그 줄이 든 값이라 다시 잰다).
- 변수 값: 말에만 있던 값(화면에는 없던 값)이 고친 줄에서 나온 것이면 고친 글에서 다시 읽는다.
  화면에서 본 값은 바꾸지 않는다. 말과 달라지면 어긋남으로 남는다 (조용히 고르지 않는다).
- 자막 규칙: 말을 받아 적는 자막인지 다시 보고, 규칙의 예가 고친 줄이었으면 고친 글로 바꾼다.
- 어긋남: 다시 견줘, 사라진 것과 새로 생긴 것을 알려 준다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

from frame.domain.script import Quantity, SpokenScript, canonical_text, quantities
from frame.domain.series.script_evidence import (
    Agreement,
    Discrepancy,
    ScriptEvidence,
    apply_script,
    link_script,
    narration_from_script,
)
from frame.domain.series.source_analysis import ScreenText
from frame.domain.series.theme import Theme


@dataclass(frozen=True)
class ScriptRefit:
    """대본의 줄을 고친 뒤 다시 맞춘 결과."""

    theme: Theme
    evidence: ScriptEvidence
    changed_lines: tuple[str, ...] = ()
    # 다시 맞춘 것: 'narration:<자리>' · 'pace' · 'variable:<키>' · 'rule:<키>'
    refit: tuple[str, ...] = ()
    resolved: tuple[Discrepancy, ...] = ()  # 사라진 어긋남
    raised: tuple[Discrepancy, ...] = ()  # 새로 생긴 어긋남


def refit_after_correction(
    theme: Theme,
    before: SpokenScript,
    after: SpokenScript,
    screen_texts: Sequence[ScreenText],
    previous: ScriptEvidence | None = None,
) -> ScriptRefit:
    """고친 줄에 걸린 것만 다시 맞춘다. 모델도 인식기도 부르지 않는다.

    - 내레이션 규칙: 고친 줄이 있는 자리의 것만 다시 잰다 (전체의 빠르기도 그 줄이 든 값이라 다시 잰다).
    - 변수 값: 말에만 있던 값(화면에는 없던 값)이 고친 줄에서 나온 것이면 고친 글에서 다시 읽는다.
      화면에서 본 값은 바꾸지 않는다. 말과 달라지면 어긋남으로 남는다.
    - 자막 규칙: 말을 받아 적는 자막인지 다시 보고, 규칙의 예가 고친 줄이었으면 고친 글로 바꾼다.
    - 어긋남: 다시 견줘, 사라진 것과 새로 생긴 것을 알려 준다.
    """
    old_evidence = previous if previous is not None else link_script(theme, before, screen_texts)
    changed = tuple(line.line_id for line in after.lines if before.line(line.line_id) != line)
    if not changed:
        return ScriptRefit(theme, old_evidence)
    refit: list[str] = []
    interim = link_script(theme, after, screen_texts)
    variables = []
    for variable in theme.variables:
        old = next((v for v in old_evidence.variables if v.key == variable.key), None)
        new = next((v for v in interim.variables if v.key == variable.key), None)
        still_said = new is not None and new.agreement in (Agreement.AGREE, Agreement.SPEECH_ONLY)
        from_speech = old is not None and old.agreement is Agreement.SPEECH_ONLY
        if from_speech and old is not None and not still_said:
            lines = [line_id for line_id in old.line_ids if line_id in changed]
            carried = _carried_value(variable.example, lines, before, after)
            if carried and carried != variable.example:
                variable = replace(variable, example=carried)
                refit.append(f"variable:{variable.key}")
        variables.append(variable)
    theme = replace(theme, variables=tuple(variables))

    fresh = narration_from_script(theme, after)
    affected = {after.line(line_id).scene_key for line_id in changed}
    beats = []
    for slot in theme.format.slots:
        old_beat, new_beat = theme.narration.beat(slot.slot_key), fresh.beat(slot.slot_key)
        if slot.slot_key not in affected:
            new_beat = old_beat
        elif new_beat != old_beat:
            refit.append(f"narration:{slot.slot_key}")
        if new_beat is not None:
            beats.append(new_beat)
    if fresh.pace_cps != theme.narration.pace_cps:
        refit.append("pace")
    narration = replace(fresh, beats=tuple(beats)) if fresh.present else fresh

    evidence = link_script(theme, after, screen_texts)
    marked = apply_script(theme, after, evidence).text_rules
    rules = []
    for old_rule, rule in zip(theme.text_rules, marked, strict=True):
        for line_id in changed if rule.follows_speech else ():
            if canonical_text(rule.example) == canonical_text(before.line(line_id).text):
                rule = replace(rule, example=after.line(line_id).text)
        if rule != old_rule:
            refit.append(f"rule:{rule.rule_key}")
        rules.append(rule)
    return ScriptRefit(
        theme=replace(theme, narration=narration, text_rules=tuple(rules)),
        evidence=evidence,
        changed_lines=changed,
        refit=tuple(refit),
        resolved=tuple(d for d in old_evidence.discrepancies if d not in evidence.discrepancies),
        raised=tuple(d for d in evidence.discrepancies if d not in old_evidence.discrepancies),
    )


def _carried_value(
    value: str, line_ids: Sequence[str], before: SpokenScript, after: SpokenScript
) -> str | None:
    """옛 줄에서 읽었던 값을 고친 줄에서 다시 읽는다. 읽을 수 없으면 None (그대로 둔다)."""
    wanted = quantities(value)
    for line_id in line_ids:
        corrected = after.line(line_id).text
        if wanted:
            if len(wanted) != 1:
                continue
            same_unit = [q for q in quantities(corrected) if q.unit == wanted[0].unit]
            if len(same_unit) == 1:
                return _restyled(value, wanted[0], same_unit[0])
            continue
        heard = before.line(line_id).text
        at = heard.find(value)
        if at < 0:
            continue
        start, end = _mapped(heard, corrected, at, at + len(value))
        carried = corrected[start:end].strip()
        if carried:
            return carried
    return None


def _restyled(value: str, old: Quantity, new: Quantity) -> str:
    """새 수량을 옛 값이 적힌 모양으로 적는다 ('5,000원' -> '5,500원'). 옛 값이 한글로 적혀 있었으면 고친 글 그대로."""
    if not any(ch.isdigit() for ch in value) or new.value != new.value.to_integral_value():
        return new.raw
    digits = format(int(new.value), ",") if "," in value else str(int(new.value))
    unit = value[old.start : old.end].lstrip("0123456789,. ")
    return f"{digits}{unit or new.unit}"


def _mapped(old: str, new: str, start: int, end: int) -> tuple[int, int]:
    """옛 글의 구간 [start, end) 이 새 글의 어디가 됐는가 (글자 단위의 고친 자국을 따라간다)."""
    new_start, new_end = 0, len(new)
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if i1 <= start < i2 or (start == i1 == i2):
            new_start = j1 + (start - i1) if tag == "equal" else j1
        if i1 < end <= i2:
            new_end = j1 + (end - i1) if tag == "equal" else j2
    return new_start, new_end
