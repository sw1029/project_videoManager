"""테마 — 편 사이의 규칙 (ADR-0025).

맡는 개념: 숏폼 한 편에서 뽑아 여러 편에 걸쳐 지키는 약속. 고정 출연, 형식(장면 자리), 스타일, 화면 문구의 틀,
내레이션·음악, 편마다 바뀌는 변수. 값은 IR 의 어휘(객체 종류·술어·값 정규화)를 그대로 쓴다.

모듈 지도
- theme          Theme (검증·자리의 조건·고정 요구조건·기준 이미지 붙이기·직렬화)
- format         SceneSlot · FormatSpec · slot_spans_from_shots (컷을 자리로 묶기)
- cast           CastMember · AnchorOrigin (기준 이미지의 출처)
- style          StyleSpec
- text_rules     TextRule · TextStyle · ThemeVariable · 문구 틀 채우기 · 색 이름
- narration_spec NarrationSpec · NarrationBeat · MusicSpec
- source         SourceVideo · SourceOrigin (원본 영상의 기록)
- conditions     Condition (술어 = 값)
"""

from .cast import AnchorOrigin, CastMember
from .conditions import Condition
from .format import DEFAULT_MIN_SLOT_MS, SLOT_GRID_MS, FormatSpec, SceneSlot, slot_spans_from_shots
from .narration_spec import MusicSpec, NarrationBeat, NarrationSpec
from .source import SourceOrigin, SourceVideo
from .style import StyleSpec
from .text_rules import (
    CSS_COLOR_NAMES,
    TextRule,
    TextStyle,
    ThemeVariable,
    VariableScope,
    color_name,
    render_template,
    template_variables,
)
from .theme import KEY_FORMAT, THEME_SCHEMA_VERSION, Theme

__all__ = [
    "AnchorOrigin",
    "CSS_COLOR_NAMES",
    "CastMember",
    "Condition",
    "DEFAULT_MIN_SLOT_MS",
    "FormatSpec",
    "KEY_FORMAT",
    "MusicSpec",
    "NarrationBeat",
    "NarrationSpec",
    "SLOT_GRID_MS",
    "SceneSlot",
    "SourceOrigin",
    "SourceVideo",
    "StyleSpec",
    "THEME_SCHEMA_VERSION",
    "TextRule",
    "TextStyle",
    "Theme",
    "ThemeVariable",
    "VariableScope",
    "color_name",
    "render_template",
    "slot_spans_from_shots",
    "template_variables",
]
