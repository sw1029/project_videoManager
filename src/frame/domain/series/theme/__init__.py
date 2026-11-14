"""테마: 영상 한 편에서 뽑아, 같은 테마의 다른 편을 만들 때 그대로 두는 것과 편마다 채우는 자리를 적은 표현 (ADR-0025).

IR 과의 관계
- IR(IntermediateRepresentation)은 영상 '한 편' 의 조건이다: 객체·장면·요구조건. 원본 영상에서 읽은 것도 IR 로 담는다.
- 테마는 IR 이 담지 못하는 '편 사이의 규칙' 이다.
  - cast: 편마다 다시 나오는 고정 출연과 그 고정 속성 (IR 의 객체 키·종류·술어 어휘를 그대로 쓴다)
  - format: 장면 자리(slot)의 순서·길이·역할과 원본의 컷 리듬
  - style: 모든 장면에 거는 장면 수준 조건(조명·분위기 등)과 색
  - text_rules: 화면 문구의 자리·모양·틀 (문구는 생성 조건이 아니라 후반 합성으로 다룬다, ADR-0027)
  - narration: 말투·빠르기·자리마다 하는 말의 구실 / music: 음악의 유무·분위기
  - variables: 편마다 채우는 값 (메뉴 이름·가격 등)
- 테마의 고정 요소는 편의 IR 을 만들 때 코드가 넣는다 (domain/episode.py). 기획 모델이 빠뜨리거나 바꿀 수 없다.

권리 (ADR-0025)
- 외부 영상에서 잘라 낸 이미지는 기준 이미지로 두지 않는다. 실존 인물의 얼굴을 복제하지 않기 위해서다.
  외부 영상에서 온 테마의 정체성 기준은 그 테마로 처음 만든 편(PILOT)이나 사용자가 준 이미지(USER)에서만 온다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from frame.domain.enums import EntityKind
from frame.domain.errors import NotFound, ValidationError
from frame.domain.hashing import canonical_hash
from frame.domain.ir import EntitySpec, RequirementSpec
from frame.domain.screen import Region, TextRole, region_or_none
from frame.domain.series.shortform import MediaInfo
from frame.domain.series.source_analysis import Shot
from frame.domain.vocabulary import DEFAULT_VOCABULARY

THEME_SCHEMA_VERSION = 1
DEFAULT_MIN_SLOT_MS = 3000  # 장면 자리의 길이 하한 (구조화 프롬프트의 장면 하한과 같다)
SLOT_GRID_MS = 100  # 자리 길이의 단위 (30 fps 에서 프레임 수가 정확히 맞는 길이)

_VARIABLE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
# 화면 문구의 색으로 받는 이름 (CSS 색 이름. ffmpeg 도 같은 이름을 안다)
CSS_COLOR_NAMES = frozenset(
    (
        "aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue "
        "blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk "
        "crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkkhaki darkmagenta "
        "darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue "
        "darkslategray darkturquoise darkviolet deeppink deepskyblue dimgray dodgerblue firebrick "
        "floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow "
        "honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon "
        "lightblue lightcoral lightcyan lightgoldenrodyellow lightgreen lightgrey lightpink "
        "lightsalmon lightseagreen lightskyblue lightslategray lightsteelblue lightyellow lime "
        "limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple "
        "mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred "
        "midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab orange "
        "orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff "
        "peru pink plum powderblue purple red rosybrown royalblue saddlebrown salmon sandybrown "
        "seagreen seashell sienna silver skyblue slateblue slategray snow springgreen steelblue tan "
        "teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen "
    ).split()
)
_VARIABLE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 자리 키는 그대로 IR 의 장면 키가 되고 (장면 키는 저장 키에 들어간다) 고정 출연 키는 객체 키가 된다.
# 그래서 IR 의 키와 같은 형식만 받는다: 영문·숫자로 시작, 영문·숫자·밑줄, 64자까지 (감사 BTS-14)
KEY_FORMAT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_]{0,63}")
_THEME_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class SourceOrigin(StrEnum):
    FRAME_GENERATED = "FRAME_GENERATED"  # FRAME 이 만든 영상 (나오는 인물은 합성 인물)
    EXTERNAL = "EXTERNAL"  # 밖에서 온 영상 (실존 인물이 나올 수 있다)


class AnchorOrigin(StrEnum):
    """고정 출연의 기준 이미지가 어디서 왔는가."""

    SOURCE = "SOURCE"  # 테마를 뽑은 원본 영상에서 잘라 냄 (FRAME 이 만든 영상일 때만)
    PILOT = "PILOT"  # 이 테마로 처음 만든 편의 클립에서 뽑음
    USER = "USER"  # 사용자가 준 이미지


class VariableScope(StrEnum):
    EPISODE = "EPISODE"  # 편마다 값 하나 (메뉴 이름·가격)
    SLOT = "SLOT"  # 장면 자리마다 값 (자리별 자막)


@dataclass(frozen=True)
class Condition:
    """술어 = 값. 술어와 값은 IR 의 요구조건과 같은 어휘 규칙으로 정규화된다."""

    predicate: str
    value: str

    def __post_init__(self) -> None:
        predicate = DEFAULT_VOCABULARY.normalize(self.predicate)
        object.__setattr__(self, "predicate", predicate)
        value = DEFAULT_VOCABULARY.normalize_value(predicate, self.value or "")
        if not value:
            raise ValidationError(f"조건 '{predicate}' 의 값이 비어 있습니다")
        object.__setattr__(self, "value", value)

    def to_dict(self) -> dict[str, str]:
        return {"predicate": self.predicate, "value": self.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Condition:
        return cls(str(data["predicate"]), str(data["value"]))


def _conditions(items: Sequence[Mapping[str, Any]] | None) -> tuple[Condition, ...]:
    return tuple(Condition.from_dict(item) for item in items or ())


@dataclass(frozen=True)
class SourceVideo:
    """테마를 뽑은 영상의 출처 기록. 영상 자체는 담지 않는다 (지문과 겉모습만)."""

    sha256: str
    media: MediaInfo
    origin: SourceOrigin = SourceOrigin.EXTERNAL
    title: str = ""
    source_url: str | None = None
    license: str | None = None
    attribution: str | None = None
    stored_uri: str | None = None  # 저장소에 사본을 둔 경우 (외부 영상은 기본으로 두지 않는다)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "media": self.media.to_dict(),
            "origin": str(self.origin),
            "title": self.title,
            "source_url": self.source_url,
            "license": self.license,
            "attribution": self.attribution,
            "stored_uri": self.stored_uri,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceVideo:
        return cls(
            sha256=str(data["sha256"]),
            media=MediaInfo.from_dict(data["media"]),
            origin=SourceOrigin(data.get("origin", SourceOrigin.EXTERNAL)),
            title=str(data.get("title", "")),
            source_url=data.get("source_url"),
            license=data.get("license"),
            attribution=data.get("attribution"),
            stored_uri=data.get("stored_uri"),
        )


@dataclass(frozen=True)
class CastMember:
    """편마다 다시 나오는 고정 출연 (인물·소품 등). 고정 속성은 그 출연이 나오는 모든 장면의 요구조건이 된다."""

    entity_key: str
    kind: EntityKind = EntityKind.OTHER
    description: str = ""
    role: str = ""  # 시리즈에서의 구실 (진행자, 소개하는 제품 …)
    fixed_attributes: tuple[Condition, ...] = ()
    reference_image_uri: str | None = None  # 정체성 기준 이미지 (ADR-0015 를 편 사이로 넓힌 것)
    reference_origin: AnchorOrigin | None = None
    reference_description: str = ""  # 기준 이미지에서 읽은 외형

    def entity_spec(self) -> EntitySpec:
        return EntitySpec(self.entity_key, self.kind, self.description)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_key": self.entity_key,
            "kind": str(self.kind),
            "description": self.description,
            "role": self.role,
            "fixed_attributes": [c.to_dict() for c in self.fixed_attributes],
            "reference_image_uri": self.reference_image_uri,
            "reference_origin": str(self.reference_origin) if self.reference_origin else None,
            "reference_description": self.reference_description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CastMember:
        origin = data.get("reference_origin")
        return cls(
            entity_key=str(data["entity_key"]),
            kind=EntityKind(data.get("kind", EntityKind.OTHER)),
            description=str(data.get("description", "")),
            role=str(data.get("role", "")),
            fixed_attributes=_conditions(data.get("fixed_attributes")),
            reference_image_uri=data.get("reference_image_uri"),
            reference_origin=AnchorOrigin(origin) if origin else None,
            reference_description=str(data.get("reference_description", "")),
        )


@dataclass(frozen=True)
class SceneSlot:
    """형식의 장면 자리 하나. 편마다 내용은 바뀌어도 순서·길이·구실은 같다."""

    slot_key: str
    duration_ms: int
    purpose: str = ""  # 이 자리의 구실 (예: 진행자가 인사한다, 제품을 가까이 보여 준다)
    cast_keys: tuple[str, ...] = ()  # 이 자리에 늘 나오는 고정 출연
    conditions: tuple[
        Condition, ...
    ] = ()  # 이 자리의 장면 수준 조건 (카메라·장소 등). 스타일의 조건을 덮는다

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_key": self.slot_key,
            "duration_ms": self.duration_ms,
            "purpose": self.purpose,
            "cast_keys": list(self.cast_keys),
            "conditions": [c.to_dict() for c in self.conditions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SceneSlot:
        return cls(
            slot_key=str(data["slot_key"]),
            duration_ms=int(data["duration_ms"]),
            purpose=str(data.get("purpose", "")),
            cast_keys=tuple(data.get("cast_keys", ())),
            conditions=_conditions(data.get("conditions")),
        )


@dataclass(frozen=True)
class FormatSpec:
    """형식: 장면 자리의 순서와 길이, 그리고 원본에서 본 컷 리듬."""

    slots: tuple[SceneSlot, ...]
    aspect_ratio: str = "9:16"
    observed_shot_ms: tuple[int, ...] = ()  # 원본의 컷 길이 (자리로 묶기 전)

    @property
    def total_duration_ms(self) -> int:
        return sum(slot.duration_ms for slot in self.slots)

    @property
    def mean_shot_ms(self) -> float | None:
        """원본의 평균 컷 길이. 컷을 재지 않았으면 자리 길이의 평균."""
        lengths = self.observed_shot_ms or tuple(slot.duration_ms for slot in self.slots)
        return sum(lengths) / len(lengths) if lengths else None

    @property
    def cuts_per_10s(self) -> float | None:
        lengths = self.observed_shot_ms or tuple(slot.duration_ms for slot in self.slots)
        total = sum(lengths)
        return (len(lengths) - 1) * 10_000 / total if total > 0 else None

    def span(self, slot_key: str) -> tuple[int, int]:
        """그 자리의 시작·끝 시각 (편의 타임라인)."""
        start = 0
        for slot in self.slots:
            if slot.slot_key == slot_key:
                return (start, start + slot.duration_ms)
            start += slot.duration_ms
        raise NotFound(f"장면 자리 '{slot_key}' 가 테마에 없습니다", slot_key=slot_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "aspect_ratio": self.aspect_ratio,
            "slots": [slot.to_dict() for slot in self.slots],
            "observed_shot_ms": list(self.observed_shot_ms),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FormatSpec:
        return cls(
            slots=tuple(SceneSlot.from_dict(s) for s in data.get("slots", ())),
            aspect_ratio=str(data.get("aspect_ratio", "9:16")),
            observed_shot_ms=tuple(int(v) for v in data.get("observed_shot_ms", ())),
        )


@dataclass(frozen=True)
class StyleSpec:
    """스타일: 모든 장면에 거는 장면 수준 조건과, 조건으로 옮기기 어려운 겉모습의 설명."""

    conditions: tuple[Condition, ...] = ()  # lighting, mood, time_of_day, style …
    palette: tuple[str, ...] = ()  # 주된 색
    notes: str = ""  # 구도·질감·편집 느낌 (글)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conditions": [c.to_dict() for c in self.conditions],
            "palette": list(self.palette),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StyleSpec:
        return cls(
            conditions=_conditions(data.get("conditions")),
            palette=tuple(str(c) for c in data.get("palette", ())),
            notes=str(data.get("notes", "")),
        )


@dataclass(frozen=True)
class TextStyle:
    """화면 문구의 겉모습. 색은 이름이나 #RRGGBB, 크기는 글자 높이 ÷ 화면 높이."""

    color: str = "white"
    background: str | None = "black"  # 글자 뒤 띠의 색. None 이면 띠 없음
    background_opacity: float = 0.55
    size: float = 0.045
    bold: bool = True

    def __post_init__(self) -> None:
        if not 0 < self.size <= 0.5:
            raise ValidationError(f"문구 크기는 화면 높이의 0~0.5 배여야 합니다: {self.size}")
        if not 0 <= self.background_opacity <= 1:
            raise ValidationError(f"띠의 불투명도는 0~1 이어야 합니다: {self.background_opacity}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "color": self.color,
            "background": self.background,
            "background_opacity": self.background_opacity,
            "size": self.size,
            "bold": self.bold,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> TextStyle:
        base = cls()
        if not data:
            return base
        return cls(
            color=str(data.get("color") or base.color),
            background=data.get("background", base.background),
            background_opacity=float(data.get("background_opacity", base.background_opacity)),
            size=float(data.get("size", base.size)),
            bold=bool(data.get("bold", base.bold)),
        )


def color_name(raw: object) -> str | None:
    """색 표기를 화면 문구가 받는 꼴로: #RRGGBB 또는 CSS 색 이름. 모르는 표기는 None.

    'Light Yellow'·'light-yellow' 는 'lightyellow' 로 맞춘다. 목록은 ffmpeg 가 아는 색 이름과 같다.
    """
    text = str(raw or "").strip()
    if _HEX_COLOR.match(text):
        return text.lower()
    name = re.sub(r"[\s_-]+", "", text.lower())
    # gray·grey 는 이름마다 받는 철자가 다르다 (lightgrey, darkgray): 둘 다 찾아본다
    for candidate in (name, name.replace("grey", "gray"), name.replace("gray", "grey")):
        if candidate in CSS_COLOR_NAMES:
            return candidate
    return None


def template_variables(template: str) -> tuple[str, ...]:
    """틀에 쓰인 변수 이름 (나온 순서, 중복 없이). '{menu} {price}' -> ('menu', 'price')."""
    seen: list[str] = []
    for name in _VARIABLE.findall(template):
        if name not in seen:
            seen.append(name)
    return tuple(seen)


def render_template(template: str, values: Mapping[str, str]) -> str:
    """틀의 {이름} 을 값으로 바꾼다. 값이 없는 변수가 있으면 ValidationError."""
    missing = [name for name in template_variables(template) if not values.get(name)]
    if missing:
        raise ValidationError(
            f"문구 틀의 변수에 값이 없습니다: {missing}", variables=tuple(missing)
        )
    return _VARIABLE.sub(lambda match: str(values[match.group(1)]), template)


@dataclass(frozen=True)
class TextRule:
    """화면 문구 규칙: 어느 자리에, 어디에, 어떤 모양으로, 어떤 틀의 문구가 나오는가."""

    rule_key: str
    template: str  # 고정 문구이거나 변수가 든 틀: "{menu} {price}"
    role: TextRole = TextRole.OTHER
    slot_keys: tuple[str, ...] = ()  # 나오는 자리. 비어 있으면 모든 자리
    region: Region | None = None  # 없으면 구실(role)에 따른 기본 자리
    start_ms: int | None = None  # 자리 안에서 나오는 시각. 없으면 자리의 처음부터
    end_ms: int | None = None  # 없으면 자리의 끝까지
    style: TextStyle = field(default_factory=TextStyle)
    example: str = ""  # 원본에서 본 문구

    @property
    def variables(self) -> tuple[str, ...]:
        return template_variables(self.template)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_key": self.rule_key,
            "template": self.template,
            "role": str(self.role),
            "slot_keys": list(self.slot_keys),
            "region": self.region.to_dict() if self.region else None,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "style": self.style.to_dict(),
            "example": self.example,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TextRule:
        return cls(
            rule_key=str(data["rule_key"]),
            template=str(data["template"]),
            role=TextRole(data.get("role", TextRole.OTHER)),
            slot_keys=tuple(data.get("slot_keys", ())),
            region=region_or_none(data.get("region")),
            start_ms=int(data["start_ms"]) if data.get("start_ms") is not None else None,
            end_ms=int(data["end_ms"]) if data.get("end_ms") is not None else None,
            style=TextStyle.from_dict(data.get("style")),
            example=str(data.get("example", "")),
        )


@dataclass(frozen=True)
class NarrationBeat:
    """자리 하나에서 하는 말의 구실과 원본의 예."""

    slot_key: str
    purpose: str = ""
    example: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"slot_key": self.slot_key, "purpose": self.purpose, "example": self.example}


@dataclass(frozen=True)
class NarrationSpec:
    present: bool = False  # 원본에 말소리가 있었는가
    language: str | None = None
    tone: str = ""  # 말투 (예: 친근한 존댓말, 빠르고 밝게)
    pace_cps: float | None = None  # 초당 글자 수. 녹음 대본의 길이 예산에 쓴다
    beats: tuple[NarrationBeat, ...] = ()

    def beat(self, slot_key: str) -> NarrationBeat | None:
        return next((b for b in self.beats if b.slot_key == slot_key), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "language": self.language,
            "tone": self.tone,
            "pace_cps": self.pace_cps,
            "beats": [b.to_dict() for b in self.beats],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> NarrationSpec:
        if not data:
            return cls()
        pace = data.get("pace_cps")
        return cls(
            present=bool(data.get("present", False)),
            language=data.get("language"),
            tone=str(data.get("tone", "")),
            pace_cps=float(pace) if pace is not None else None,
            beats=tuple(
                NarrationBeat(
                    str(b["slot_key"]), str(b.get("purpose", "")), str(b.get("example", ""))
                )
                for b in data.get("beats", ())
            ),
        )


@dataclass(frozen=True)
class MusicSpec:
    present: bool | None = None  # None 이면 알 수 없음 (소리를 분석하지 않았다)
    mood: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"present": self.present, "mood": self.mood, "notes": self.notes}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> MusicSpec:
        if not data:
            return cls()
        return cls(data.get("present"), str(data.get("mood", "")), str(data.get("notes", "")))


@dataclass(frozen=True)
class ThemeVariable:
    """편마다 채우는 값의 자리."""

    key: str
    description: str = ""
    example: str = ""  # 원본에서의 값
    scope: VariableScope = VariableScope.EPISODE

    def to_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "description": self.description,
            "example": self.example,
            "scope": str(self.scope),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ThemeVariable:
        return cls(
            key=str(data["key"]),
            description=str(data.get("description", "")),
            example=str(data.get("example", "")),
            scope=VariableScope(data.get("scope", VariableScope.EPISODE)),
        )


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


def slot_spans_from_shots(
    shots: Sequence[Shot],
    *,
    min_slot_ms: int = DEFAULT_MIN_SLOT_MS,
    grid_ms: int = SLOT_GRID_MS,
) -> tuple[tuple[int, int], ...]:
    """원본의 컷(샷)을 다시 만들 수 있는 장면 자리의 구간으로 묶는다.

    - 길이가 min_slot_ms 에 못 미치는 샷은 뒤의 샷과 묶는다 (마지막에 남으면 앞 자리에 붙인다).
      영상 전체가 min_slot_ms 보다 짧으면 자리 하나가 된다.
    - 자리의 경계는 grid_ms 단위로 맞춘다 (장면 길이가 프레임 수와 정확히 맞게).
    결과는 0 ms 에서 시작해 빈틈 없이 이어지는 (시작, 끝) 구간이다.
    """
    if not shots:
        raise ValidationError("샷이 하나 이상 있어야 합니다")
    if min_slot_ms <= 0 or grid_ms <= 0:
        raise ValidationError("자리 길이의 하한과 단위는 0 보다 커야 합니다")
    groups: list[list[int]] = []  # [시작, 끝]
    for shot in shots:
        if groups and groups[-1][1] - groups[-1][0] < min_slot_ms:
            groups[-1][1] = shot.end_ms
        else:
            groups.append([shot.start_ms, shot.end_ms])
    if len(groups) > 1 and groups[-1][1] - groups[-1][0] < min_slot_ms:
        tail = groups.pop()
        groups[-1][1] = tail[1]
    spans: list[tuple[int, int]] = []
    cursor = 0
    for _, end in groups:
        snapped = max(cursor + grid_ms, round(end / grid_ms) * grid_ms)
        spans.append((cursor, snapped))
        cursor = snapped
    return tuple(spans)
