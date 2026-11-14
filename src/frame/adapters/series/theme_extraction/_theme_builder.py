"""모델의 응답에서 테마·원본 IR·화면 문구를 만든다 (ADR-0025). 결정적, 순수 함수.

- 자리의 키·시간은 코드가 정한다. 모델은 자리를 번호로만 가리킨다.
- 관계·일시적 술어는 고정 속성이 될 수 없다. 배경·문구 객체는 고정 출연이 되지 않는다.
- 카메라 조건은 스타일에 두지 않는다 (자리마다 다르다).
- 얹은 문구를 객체로 적어 온 것은 원본 IR 에서 뺀다. 화면 문구와 문구 규칙으로만 남긴다.
- 말 빠르기·자리별 말의 예는 모델이 아니라 받아 적은 말소리에서 계산한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from frame.application.ports.series.theme_extraction import (
    ThemeExtractionRequest,
)
from frame.domain.enums import EntityKind
from frame.domain.ir import EntitySpec, IntermediateRepresentation, RequirementSpec, SceneSpec
from frame.domain.screen import TextRole, TextSource
from frame.domain.series.source_analysis import ScreenText
from frame.domain.series.theme import (
    CastMember,
    Condition,
    FormatSpec,
    MusicSpec,
    NarrationBeat,
    NarrationSpec,
    SceneSlot,
    StyleSpec,
    TextRule,
    TextStyle,
    Theme,
    ThemeVariable,
    VariableScope,
    color_name,
    template_variables,
)
from frame.domain.vocabulary import DEFAULT_VOCABULARY, ValueKind

from ._answer_parts import (
    _CAST_KINDS,
    _SLOT_ONLY,
    _check_shapes,
    _conditions,
    _entities,
    _lookup,
    _mapping,
    _plain,
    _region,
    _requirements,
    _scene_conditions,
    _slot_answers,
    _slot_index,
    _squash,
    _unique,
)


# ---- 응답을 테마와 원본 IR 로 다듬기 (결정적, 순수 함수) -----------------------------------------
def build_theme(
    request: ThemeExtractionRequest,
    spans: Sequence[tuple[int, int]],
    document: Mapping[str, Any],
) -> tuple[Theme, IntermediateRepresentation, tuple[ScreenText, ...], list[str]]:
    """모델의 응답(document)에서 규칙에 맞는 것만 골라 테마·원본 IR·화면 문구를 만든다.

    규칙을 통과하지 못하면 ValidationError (부른 쪽이 모델에 되돌려 보낸다).
    """
    _check_shapes(document)
    notes: list[str] = []
    slot_keys = [f"slot_{i}" for i in range(1, len(spans) + 1)]
    answers = _slot_answers(document.get("slots"), len(spans))
    key_of, entities, raw_entities = _entities(document.get("entities"), notes)
    kinds = {e.entity_key: e.kind for e in entities}

    cast = _cast(raw_entities, key_of, entities, notes)
    cast_keys = {c.entity_key for c in cast}
    style_conditions = [
        condition
        for condition in _scene_conditions(
            _mapping(document.get("style")).get("conditions"), notes, "스타일"
        )
        if condition.predicate not in _SLOT_ONLY
    ]
    style_by_predicate = {c.predicate: c for c in style_conditions}

    overlay_texts = {
        _squash(item.get("text"))
        for item in document.get("screen_texts") or ()
        if isinstance(item, dict) and str(item.get("source") or "").upper() != "IN_SCENE"
    } - {""}
    overlay_entities: set[str] = set()
    scenes: list[SceneSpec] = []
    slots: list[SceneSlot] = []
    for index, (slot_key, (start, end)) in enumerate(zip(slot_keys, spans, strict=True)):
        answer = answers.get(index, {})
        entity_keys = _unique(
            key
            for key in (_lookup(k, key_of) for k in answer.get("entity_keys") or ())
            if key is not None
        )
        requirements = _requirements(
            slot_key, answer.get("requirements"), key_of, kinds, entity_keys, notes
        )
        # 편집에서 얹은 문구를 객체로 적어 온 것은 뺀다: 얹은 문구는 생성 조건이 아니다 (ADR-0027)
        captions = {
            r.entity_key
            for r in requirements
            if kinds.get(r.entity_key or "") is EntityKind.TEXT
            and DEFAULT_VOCABULARY.value_kind(r.predicate) is ValueKind.LITERAL
            and _squash(r.expected_value) in overlay_texts
        }
        if captions:
            overlay_entities.update(k for k in captions if k is not None)
            requirements = [r for r in requirements if r.entity_key not in captions]
            entity_keys = [k for k in entity_keys if k not in captions]
        # 고정 출연의 고정 속성과 스타일의 조건은 그 장면의 요구조건이기도 하다 (모델이 빠뜨려도 넣는다)
        seen = {r.key for r in requirements}
        for member in cast:
            if member.entity_key not in entity_keys:
                continue
            for condition in member.fixed_attributes:
                extra = RequirementSpec(
                    slot_key, condition.predicate, condition.value, member.entity_key
                )
                if extra.key not in seen:
                    requirements.append(extra)
                    seen.add(extra.key)
        own_scene_level = [r for r in requirements if r.entity_key is None]
        for condition in style_conditions:
            extra = RequirementSpec(slot_key, condition.predicate, condition.value)
            if extra.key not in seen:
                requirements.append(extra)
                seen.add(extra.key)
        description = " ".join(str(answer.get("description") or "").split())
        purpose = " ".join(str(answer.get("purpose") or "").split())
        scenes.append(
            SceneSpec(
                slot_key,
                start,
                end,
                description or purpose,
                tuple(entity_keys),
                tuple(requirements),
            )
        )
        slots.append(
            SceneSlot(
                slot_key=slot_key,
                duration_ms=end - start,
                purpose=purpose or description,
                cast_keys=tuple(k for k in entity_keys if k in cast_keys),
                # 이 자리만의 장면 조건: 스타일에 없거나 스타일과 값이 다른 것
                conditions=tuple(
                    Condition(r.predicate, r.expected_value)
                    for r in own_scene_level
                    if style_by_predicate.get(r.predicate) is None
                    or style_by_predicate[r.predicate].value != r.expected_value
                ),
            )
        )
    if overlay_entities:
        notes.append(
            f"편집에서 얹은 문구를 객체로 적은 것을 원본 IR 에서 뺐습니다: {sorted(overlay_entities)}"
        )
    used = {key for scene in scenes for key in scene.entity_keys}
    dropped = [e.entity_key for e in entities if e.entity_key not in used]
    if dropped:
        notes.append(f"어느 자리에도 나오지 않는 객체를 뺐습니다: {dropped}")
    source_ir = IntermediateRepresentation(
        entities=tuple(e for e in entities if e.entity_key in used), scenes=tuple(scenes)
    )
    source_ir.validate()

    variables = _variables(document.get("variables"), notes)
    rules, variables = _text_rules(document.get("text_rules"), slot_keys, variables, notes)
    transcript = request.analysis.transcript
    spoken = transcript is not None and not transcript.is_empty
    beats: list[NarrationBeat] = []
    if spoken:
        assert transcript is not None
        for index, (slot_key, (start, end)) in enumerate(zip(slot_keys, spans, strict=True)):
            said = " ".join(s.text.strip() for s in transcript.within(start, end))
            purpose = " ".join(str(answers.get(index, {}).get("narration_purpose") or "").split())
            if said or purpose:
                beats.append(NarrationBeat(slot_key, purpose, said))
    music = _mapping(document.get("music"))
    style = _mapping(document.get("style"))
    media = request.analysis.media
    theme = Theme(
        theme_id=request.theme_id,
        title=request.title.strip()
        or str(document.get("title") or "").strip()
        or request.source.title
        or "테마",
        summary=" ".join(str(document.get("summary") or "").split()),
        source=request.source,
        format=FormatSpec(
            slots=tuple(slots),
            aspect_ratio=media.aspect_label,
            observed_shot_ms=tuple(s.duration_ms for s in request.analysis.shots),
        ),
        cast=tuple(c for c in cast if c.entity_key in used),
        style=StyleSpec(
            conditions=tuple(style_conditions),
            palette=tuple(str(c).strip() for c in style.get("palette") or () if str(c).strip())[:6],
            notes=" ".join(str(style.get("notes") or "").split()),
        ),
        text_rules=tuple(rules),
        narration=NarrationSpec(
            present=spoken,
            language=transcript.language if spoken and transcript is not None else None,
            tone=" ".join(str(document.get("narration_tone") or "").split()) if spoken else "",
            pace_cps=transcript.pace_cps if spoken and transcript is not None else None,
            beats=tuple(beats),
        ),
        music=MusicSpec(
            present=music.get("present") if isinstance(music.get("present"), bool) else None,
            mood=" ".join(str(music.get("mood") or "").split()),
        ),
        variables=tuple(variables),
    )
    theme.validate()
    texts = _screen_texts(document.get("screen_texts"), spans)
    return theme, source_ir, texts, notes


def _cast(
    originals: Mapping[str, Mapping[str, Any]],
    key_of: Mapping[str, str],
    entities: Sequence[EntitySpec],
    notes: list[str],
) -> list[CastMember]:
    cast: list[CastMember] = []
    for entity in entities:
        item = originals[entity.entity_key]
        if not item.get("recurring") or entity.kind not in _CAST_KINDS:
            continue
        fixed = []
        for condition in _conditions(item.get("fixed_attributes"), notes, f"'{entity.entity_key}'"):
            if DEFAULT_VOCABULARY.is_relational(
                condition.predicate
            ) or DEFAULT_VOCABULARY.is_transient(condition.predicate):
                notes.append(
                    f"'{entity.entity_key}' 의 '{condition.predicate}' 는 고정 속성이 될 수 없어 뺐습니다"
                )
                continue
            fixed.append(condition)
        cast.append(
            CastMember(
                entity_key=entity.entity_key,
                kind=entity.kind,
                description=entity.description,
                role=" ".join(str(item.get("role") or "").split()),
                fixed_attributes=tuple(fixed),
            )
        )
    return cast


def _variables(raw: Any, notes: list[str]) -> list[ThemeVariable]:
    variables: list[ThemeVariable] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        key = _plain(item.get("key"))
        if not key or key[0].isdigit() or key in seen:
            continue
        seen.add(key)
        try:
            scope = VariableScope(str(item.get("scope") or "EPISODE").upper())
        except ValueError:
            scope = VariableScope.EPISODE
        variables.append(
            ThemeVariable(
                key,
                " ".join(str(item.get("description") or "").split()),
                " ".join(str(item.get("example") or "").split()),
                scope,
            )
        )
    return variables


def _text_rules(
    raw: Any, slot_keys: Sequence[str], variables: list[ThemeVariable], notes: list[str]
) -> tuple[list[TextRule], list[ThemeVariable]]:
    rules: list[TextRule] = []
    declared = {v.key for v in variables}
    seen: set[str] = set()
    for position, item in enumerate(raw if isinstance(raw, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        template = " ".join(str(item.get("template") or "").split())
        if not template:
            continue
        # 틀의 변수 이름도 변수 키와 같은 규칙으로 맞춘다
        for name in template_variables(template):
            template = template.replace("{" + name + "}", "{" + (_plain(name) or name) + "}")
        key = _plain(item.get("rule_key")) or f"rule_{position}"
        if key in seen:
            key = f"{key}_{position}"
        seen.add(key)
        for name in template_variables(template):
            if name not in declared:
                declared.add(name)
                variables.append(ThemeVariable(name))
                notes.append(f"문구 규칙 '{key}' 가 쓰는 변수 '{name}' 를 테마의 변수로 더했습니다")
        try:
            role = TextRole(str(item.get("role") or "").upper())
        except ValueError:
            role = TextRole.OTHER
        slots = _unique(
            slot_keys[index]
            for index in (_slot_index(s, len(slot_keys)) for s in item.get("slots") or ())
            if index is not None
        )
        base = TextStyle()
        background = item.get("background")
        style = TextStyle(
            color=color_name(item.get("color")) or base.color,
            # 띠가 없다고 한 것(null)은 그대로, 모르는 색 표기는 기본 띠로
            background=None if background is None else (color_name(background) or base.background),
        )
        rules.append(
            TextRule(
                rule_key=key,
                template=template,
                role=role,
                slot_keys=tuple(slots),
                region=_region(item.get("box")),
                style=style,
                example=" ".join(str(item.get("example") or "").split()),
            )
        )
    return rules, variables


def _screen_texts(raw: Any, spans: Sequence[tuple[int, int]]) -> tuple[ScreenText, ...]:
    texts: list[ScreenText] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        index = _slot_index(item.get("slot"), len(spans))
        if not text or index is None:
            continue
        start, end = spans[index]
        try:
            role = TextRole(str(item.get("role") or "").upper())
        except ValueError:
            role = TextRole.OTHER
        try:
            source = TextSource(str(item.get("source") or "").upper())
        except ValueError:
            source = TextSource.OVERLAY
        texts.append(
            ScreenText(
                text=text,
                start_ms=start,
                end_ms=end,
                region=_region(item.get("box")),
                role=role,
                source=source,
                style=" ".join(str(item.get("style") or "").split()),
            )
        )
    return tuple(texts)
