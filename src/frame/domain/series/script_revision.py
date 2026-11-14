"""편의 녹음 대본만 고친다 (ADR-0029). 순수 함수: 계획 -> 계획. 영상의 조건(IR)은 건드리지 않는다.

- 고치는 것: 대본 줄의 글과 그 어림 길이, 그리고 그 줄을 따르는 자막
  (말을 받아 적는 자막 규칙, TextRule.follows_speech).
- 고치지 않는 것: IR, 편의 변수 값, 변수로 만든 화면 문구 (가격표 같은 것). 영상은 다시 만들어지지 않는다.
- 줄의 길이: 빠르기를 올려도(max_tempo) 그 장면에서 말할 수 없는 줄은 받지 않는다.
  사람이 녹음하므로 뒤에서 줄일 수 없다.
- 알리는 것: 고친 줄에서 편의 변수 값(가격·상품 이름)이 빠졌거나 다른 값이 됐으면 알린다. 화면 문구는 그대로이므로
  말과 화면이 어긋나게 된다. 변수를 바꿀지는 사람이 정한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from frame.domain.errors import ValidationError
from frame.domain.script import mentions, quantity_conflicts
from frame.domain.series.episode import DEFAULT_POLICY, EpisodePlan, PlanPolicy, ScriptLine
from frame.domain.series.narration import DEFAULT_NARRATION_POLICY
from frame.domain.series.theme import Theme, render_template
from frame.domain.speech import spoken_characters


@dataclass(frozen=True)
class ScriptEdit:
    line_key: str
    text: str


@dataclass(frozen=True)
class ScriptRevision:
    plan: EpisodePlan
    changed_lines: tuple[str, ...] = ()
    changed_overlays: tuple[str, ...] = ()  # 대본을 따라 바뀐 자막
    warnings: tuple[str, ...] = ()


def revise_script(
    plan: EpisodePlan,
    theme: Theme | None,
    edits: Sequence[ScriptEdit],
    *,
    policy: PlanPolicy = DEFAULT_POLICY,
    max_tempo: float = DEFAULT_NARRATION_POLICY.max_tempo,
) -> ScriptRevision:
    """대본 줄을 고친 새 계획. theme 은 말 빠르기와 '말을 따르는 자막 규칙' 을 알려 준다 (없으면 줄만 고친다)."""
    keys = [edit.line_key for edit in edits]
    if len(keys) != len(set(keys)):
        raise ValidationError(f"같은 줄을 두 번 고칠 수 없습니다: {keys}")
    pace = (theme.narration.pace_cps if theme is not None else None) or policy.default_pace_cps
    following = {
        rule.rule_key: rule for rule in (theme.text_rules if theme else ()) if rule.follows_speech
    }
    script = list(plan.script)
    changed_lines: list[str] = []
    changed_overlays: list[str] = []
    warnings: list[str] = []
    revised = plan
    for edit in edits:
        old = plan.line(edit.line_key)  # 없는 줄이면 NotFound
        text = " ".join(edit.text.split())
        if not text:
            raise ValidationError(f"대본 줄 '{edit.line_key}' 의 글이 비어 있습니다")
        if text == old.text:
            continue
        line = replace(old, text=text, estimated_ms=round(spoken_characters(text) / pace * 1000))
        _check_fits(line, pace, max_tempo)
        script[script.index(old)] = line
        changed_lines.append(line.line_key)
        warnings += _value_warnings(plan, old, line)
        for item in revised.overlays_for(line.scene_key):
            rule = following.get(item.rule_key or "")
            if rule is None or len(rule.variables) != 1:
                continue
            caption = render_template(rule.template, {rule.variables[0]: text})
            if caption != item.text:
                revised = revised.with_overlay(replace(item, text=caption))
                changed_overlays.append(item.overlay_id)
    revised = replace(revised, script=tuple(script))
    return ScriptRevision(revised, tuple(changed_lines), tuple(changed_overlays), tuple(warnings))


def _check_fits(line: ScriptLine, pace: float, max_tempo: float) -> None:
    if line.estimated_ms is None or line.estimated_ms <= line.budget_ms * max_tempo:
        return
    limit = int(line.budget_ms * max_tempo / 1000 * pace)
    raise ValidationError(
        f"대본 줄 '{line.line_key}' 이 너무 깁니다: {spoken_characters(line.text)}자 "
        f"(이 장면에서 말할 수 있는 것은 {limit}자쯤입니다)",
        line_key=line.line_key,
        limit_chars=limit,
    )


def _value_warnings(plan: EpisodePlan, old: ScriptLine, new: ScriptLine) -> list[str]:
    """고친 줄에서 편의 변수 값이 빠졌거나 다른 값이 됐는가."""
    warnings: list[str] = []
    for key, value in plan.variables.items():
        if not value.strip() or mentions(new.text, value):
            continue
        conflicts = quantity_conflicts(value, new.text)
        if conflicts:
            said = ", ".join(
                f"{format(v.normalize(), 'f')}{c.unit}" for c in conflicts for v in c.right
            )
            warnings.append(
                f"대본 줄 '{new.line_key}' 이 말하는 {said} 이(가) 편의 변수 '{key}' 의 값 "
                f"'{value}' 과 다릅니다. 화면 문구는 바뀌지 않았습니다"
            )
        elif mentions(old.text, value):
            warnings.append(
                f"대본 줄 '{new.line_key}' 에서 편의 변수 '{key}' 의 값 '{value}' 이(가) 빠졌습니다. "
                "화면 문구에는 그대로 있습니다"
            )
    return warnings
