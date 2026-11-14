"""테마: 편 사이의 규칙을 한데 묶은 것 (ADR-0025).

IR 이 영상 한 편의 조건이라면 테마는 편이 바뀌어도 같아야 하는 것이다. Theme.validate 가 규칙을 지키게 한다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from frame.domain.errors import NotFound, ValidationError
from frame.domain.hashing import canonical_hash
from frame.domain.ir import RequirementSpec
from frame.domain.vocabulary import DEFAULT_VOCABULARY

from .cast import AnchorOrigin, CastMember
from .conditions import Condition
from .format import FormatSpec, SceneSlot
from .narration_spec import MusicSpec, NarrationSpec
from .source import SourceOrigin, SourceVideo
from .style import StyleSpec
from .text_rules import _VARIABLE_KEY, TextRule, ThemeVariable

THEME_SCHEMA_VERSION = 1


# 자리 키는 그대로 IR 의 장면 키가 되고 (장면 키는 저장 키에 들어간다) 고정 출연 키는 객체 키가 된다.
# 그래서 IR 의 키와 같은 형식만 받는다: 영문·숫자로 시작, 영문·숫자·밑줄, 64자까지 (감사 BTS-14)
KEY_FORMAT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_]{0,63}")


_THEME_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


@dataclass(frozen=True)
class Theme:
    theme_id: str
    title: str
    source: SourceVideo
    format: FormatSpec
    cast: tuple[CastMember, ...] = ()
    style: StyleSpec = field(default_factory=StyleSpec)
    text_rules: tuple[TextRule, ...] = ()
    narration: NarrationSpec = field(default_factory=NarrationSpec)
    music: MusicSpec = field(default_factory=MusicSpec)
    variables: tuple[ThemeVariable, ...] = ()
    summary: str = ""

    # ---- 조회 -------------------------------------------------------------
    def has_cast(self, entity_key: str) -> bool:
        return any(c.entity_key == entity_key for c in self.cast)

    def cast_member(self, entity_key: str) -> CastMember:
        for member in self.cast:
            if member.entity_key == entity_key:
                return member
        raise NotFound(f"고정 출연 '{entity_key}' 가 테마에 없습니다", entity_key=entity_key)

    def slot(self, slot_key: str) -> SceneSlot:
        for slot in self.format.slots:
            if slot.slot_key == slot_key:
                return slot
        raise NotFound(f"장면 자리 '{slot_key}' 가 테마에 없습니다", slot_key=slot_key)

    def variable(self, key: str) -> ThemeVariable | None:
        return next((v for v in self.variables if v.key == key), None)

    def scene_conditions(self, slot_key: str) -> tuple[Condition, ...]:
        """그 자리의 장면 수준 조건: 스타일의 조건에 자리의 조건을 덮어쓴 것 (스타일 순서, 그 뒤 자리 고유의 것)."""
        own = {c.predicate: c for c in self.slot(slot_key).conditions}
        merged = [own.pop(c.predicate, c) for c in self.style.conditions]
        return (*merged, *own.values())

    def fixed_requirements(
        self, slot_key: str, entity_keys: Sequence[str]
    ) -> tuple[RequirementSpec, ...]:
        """그 자리의 장면에 테마가 거는 요구조건: 나오는 고정 출연의 고정 속성 + 장면 수준 조건."""
        requirements = [
            RequirementSpec(slot_key, c.predicate, c.value, member.entity_key)
            for member in self.cast
            if member.entity_key in entity_keys
            for c in member.fixed_attributes
        ]
        requirements += [
            RequirementSpec(slot_key, c.predicate, c.value) for c in self.scene_conditions(slot_key)
        ]
        return tuple(requirements)

    def references(self) -> tuple[CastMember, ...]:
        """기준 이미지가 있는 고정 출연."""
        return tuple(c for c in self.cast if c.reference_image_uri)

    def with_reference(
        self,
        entity_key: str,
        image_uri: str,
        origin: AnchorOrigin,
        description: str = "",
    ) -> Theme:
        """고정 출연에 기준 이미지를 붙인 새 테마. 규칙을 어기면 ValidationError."""
        self.cast_member(entity_key)  # 없으면 NotFound
        updated = replace(
            self,
            cast=tuple(
                replace(
                    c,
                    reference_image_uri=image_uri,
                    reference_origin=origin,
                    reference_description=description,
                )
                if c.entity_key == entity_key
                else c
                for c in self.cast
            ),
        )
        updated.validate()
        return updated

    # ---- 규칙 -------------------------------------------------------------
    def validate(self) -> None:
        if not self.theme_id.strip() or not self.title.strip():
            raise ValidationError("테마에는 id 와 제목이 있어야 합니다")
        if not _THEME_ID.fullmatch(self.theme_id):
            raise ValidationError(
                f"테마 id 의 형식이 잘못되었습니다: {self.theme_id!r} (영문·숫자·밑줄·붙임표, 64자까지)"
            )
        self._validate_format()
        self._validate_cast()
        _require_scene_level("스타일", self.style.conditions)
        self._validate_variables()
        self._validate_text_rules()
        slot_keys = {slot.slot_key for slot in self.format.slots}
        beats = [b.slot_key for b in self.narration.beats]
        if len(beats) != len(set(beats)) or not set(beats) <= slot_keys:
            raise ValidationError(f"내레이션의 자리가 중복되거나 테마에 없습니다: {beats}")
        if self.narration.pace_cps is not None and self.narration.pace_cps <= 0:
            raise ValidationError(f"말 빠르기가 잘못되었습니다: {self.narration.pace_cps}")

    def _validate_format(self) -> None:
        slots = self.format.slots
        if not slots:
            raise ValidationError("테마에는 장면 자리가 하나 이상 있어야 합니다")
        keys = [slot.slot_key for slot in slots]
        if len(keys) != len(set(keys)) or not all(k.strip() for k in keys):
            raise ValidationError(f"장면 자리의 키는 비어 있지 않고 유일해야 합니다: {keys}")
        _require_keys("장면 자리", keys)
        cast_keys = {c.entity_key for c in self.cast}
        for slot in slots:
            if slot.duration_ms <= 0:
                raise ValidationError(f"장면 자리 '{slot.slot_key}' 의 길이가 0 이하입니다")
            unknown = sorted(set(slot.cast_keys) - cast_keys)
            if unknown:
                raise ValidationError(
                    f"장면 자리 '{slot.slot_key}' 가 테마에 없는 고정 출연을 가리킵니다: {unknown}"
                )
            _require_scene_level(f"장면 자리 '{slot.slot_key}'", slot.conditions)

    def _validate_cast(self) -> None:
        keys = [c.entity_key for c in self.cast]
        if len(keys) != len(set(keys)) or not all(k.strip() for k in keys):
            raise ValidationError(f"고정 출연의 키는 비어 있지 않고 유일해야 합니다: {keys}")
        _require_keys("고정 출연", keys)
        for member in self.cast:
            seen: set[str] = set()
            for condition in member.fixed_attributes:
                if DEFAULT_VOCABULARY.is_relational(condition.predicate):
                    raise ValidationError(
                        f"고정 출연 '{member.entity_key}' 의 고정 속성에 관계 술어 "
                        f"'{condition.predicate}' 를 쓸 수 없습니다"
                    )
                if DEFAULT_VOCABULARY.is_transient(condition.predicate):
                    raise ValidationError(
                        f"'{condition.predicate}' 는 장면마다 달라지는 술어라 고정 출연 "
                        f"'{member.entity_key}' 의 고정 속성이 될 수 없습니다"
                    )
                if condition.predicate in seen:
                    raise ValidationError(
                        f"고정 출연 '{member.entity_key}' 의 고정 속성 '{condition.predicate}' 가 중복됩니다"
                    )
                seen.add(condition.predicate)
            if bool(member.reference_image_uri) != (member.reference_origin is not None):
                raise ValidationError(
                    f"고정 출연 '{member.entity_key}' 의 기준 이미지와 그 출처는 함께 있어야 합니다"
                )
            if (
                member.reference_origin is AnchorOrigin.SOURCE
                and self.source.origin is SourceOrigin.EXTERNAL
            ):
                raise ValidationError(
                    f"외부 영상에서 잘라 낸 이미지는 기준 이미지로 쓸 수 없습니다 "
                    f"(고정 출연 '{member.entity_key}'). 처음 만든 편이나 사용자가 준 이미지를 쓰세요"
                )

    def _validate_variables(self) -> None:
        keys = [v.key for v in self.variables]
        if len(keys) != len(set(keys)):
            raise ValidationError(f"변수의 키가 중복됩니다: {keys}")
        bad = [k for k in keys if not _VARIABLE_KEY.match(k)]
        if bad:
            raise ValidationError(f"변수의 키는 영문·숫자·밑줄이어야 합니다: {bad}")

    def _validate_text_rules(self) -> None:
        keys = [r.rule_key for r in self.text_rules]
        if len(keys) != len(set(keys)) or not all(k.strip() for k in keys):
            raise ValidationError(f"문구 규칙의 키는 비어 있지 않고 유일해야 합니다: {keys}")
        slot_durations = {slot.slot_key: slot.duration_ms for slot in self.format.slots}
        declared = {v.key for v in self.variables}
        for rule in self.text_rules:
            if not rule.template.strip():
                raise ValidationError(f"문구 규칙 '{rule.rule_key}' 의 틀이 비어 있습니다")
            unknown = sorted(set(rule.slot_keys) - set(slot_durations))
            if unknown:
                raise ValidationError(
                    f"문구 규칙 '{rule.rule_key}' 가 테마에 없는 자리를 가리킵니다: {unknown}"
                )
            undeclared = sorted(set(rule.variables) - declared)
            if undeclared:
                raise ValidationError(
                    f"문구 규칙 '{rule.rule_key}' 의 변수가 테마에 없습니다: {undeclared}"
                )
            start = rule.start_ms or 0
            for slot_key in rule.slot_keys or tuple(slot_durations):
                end = rule.end_ms if rule.end_ms is not None else slot_durations[slot_key]
                if start < 0 or end <= start or end > slot_durations[slot_key]:
                    raise ValidationError(
                        f"문구 규칙 '{rule.rule_key}' 의 시간 {start}~{end} ms 가 "
                        f"자리 '{slot_key}' ({slot_durations[slot_key]} ms) 를 벗어납니다"
                    )

    # ---- 직렬화 -----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": THEME_SCHEMA_VERSION,
            "theme_id": self.theme_id,
            "title": self.title,
            "summary": self.summary,
            "source": self.source.to_dict(),
            "format": self.format.to_dict(),
            "cast": [c.to_dict() for c in self.cast],
            "style": self.style.to_dict(),
            "text_rules": [r.to_dict() for r in self.text_rules],
            "narration": self.narration.to_dict(),
            "music": self.music.to_dict(),
            "variables": [v.to_dict() for v in self.variables],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Theme:
        version = data.get("schema_version", THEME_SCHEMA_VERSION)
        if version != THEME_SCHEMA_VERSION:
            raise ValidationError(
                f"지원하지 않는 테마 schema_version 입니다: {version} (지원: {THEME_SCHEMA_VERSION})"
            )
        return cls(
            theme_id=str(data["theme_id"]),
            title=str(data["title"]),
            summary=str(data.get("summary", "")),
            source=SourceVideo.from_dict(data["source"]),
            format=FormatSpec.from_dict(data["format"]),
            cast=tuple(CastMember.from_dict(c) for c in data.get("cast", ())),
            style=StyleSpec.from_dict(data.get("style", {})),
            text_rules=tuple(TextRule.from_dict(r) for r in data.get("text_rules", ())),
            narration=NarrationSpec.from_dict(data.get("narration")),
            music=MusicSpec.from_dict(data.get("music")),
            variables=tuple(ThemeVariable.from_dict(v) for v in data.get("variables", ())),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> Theme:
        return cls.from_dict(json.loads(text))

    def fingerprint(self) -> str:
        """테마 내용의 해시 (id 제외). 같은 내용의 테마는 값이 같다."""
        payload = self.to_dict()
        payload.pop("theme_id")
        return canonical_hash(payload)


def _require_keys(owner: str, keys: Sequence[str]) -> None:
    bad = [key for key in keys if not KEY_FORMAT.fullmatch(key)]
    if bad:
        raise ValidationError(
            f"{owner}의 키는 영문·숫자로 시작하고 영문·숫자·밑줄만 쓸 수 있습니다 (64자까지): {bad}"
        )


def _require_scene_level(owner: str, conditions: Sequence[Condition]) -> None:
    seen: set[str] = set()
    for condition in conditions:
        if DEFAULT_VOCABULARY.is_relational(condition.predicate):
            raise ValidationError(
                f"{owner} 의 조건에 관계 술어 '{condition.predicate}' 를 쓸 수 없습니다"
            )
        if condition.predicate in seen:
            raise ValidationError(f"{owner} 의 조건 '{condition.predicate}' 가 중복됩니다")
        seen.add(condition.predicate)
