"""화면 문구의 틀과 변수 (ADR-0025·0027).

문구 규칙은 틀("{product_name} {price}")·구실·나오는 자리·영역·글자 모양이다. 편을 만들 때 변수 값을 넣어
화면 문구가 된다. 색은 ffmpeg 가 아는 CSS 색 이름이나 #RRGGBB 로만 받는다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from frame.domain.errors import ValidationError
from frame.domain.screen import Region, TextRole, region_or_none

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


class VariableScope(StrEnum):
    EPISODE = "EPISODE"  # 편마다 값 하나 (메뉴 이름·가격)
    SLOT = "SLOT"  # 장면 자리마다 값 (자리별 자막)


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
