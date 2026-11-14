"""테마·편 테스트 공용 자료: 카페 숏폼(대조군 c3 와 같은 구성)에서 뽑았다고 본 테마."""

from __future__ import annotations

from frame.domain.enums import EntityKind
from frame.domain.ir import EntitySpec, RequirementSpec
from frame.domain.media import MediaInfo
from frame.domain.screen import Region, TextRole
from frame.domain.series.episode import EpisodeBrief, EpisodeContent, SlotContent
from frame.domain.series.theme import (
    CastMember,
    Condition,
    FormatSpec,
    MusicSpec,
    NarrationBeat,
    NarrationSpec,
    SceneSlot,
    SourceOrigin,
    SourceVideo,
    StyleSpec,
    TextRule,
    TextStyle,
    Theme,
    ThemeVariable,
    VariableScope,
)

SOURCE_SHA = "a" * 64


def cafe_source(origin: SourceOrigin = SourceOrigin.FRAME_GENERATED) -> SourceVideo:
    return SourceVideo(
        sha256=SOURCE_SHA,
        media=MediaInfo(480, 854, 16000, fps=30.0, has_audio=True, size_bytes=2_000_000),
        origin=origin,
        title="카페 신제품 홍보 숏폼",
    )


def cafe_theme(
    theme_id: str = "thm_cafe", origin: SourceOrigin = SourceOrigin.FRAME_GENERATED
) -> Theme:
    """4초 장면 4개: 인사 -> 제품 들어 보이기 -> 제품 클로즈업 -> 마무리 인사.

    고정 출연은 바리스타(흰 셔츠·갈색 앞치마)와 머그컵(세라믹), 편마다 바뀌는 값은 메뉴·가격·자리별 자막.
    """
    return Theme(
        theme_id=theme_id,
        title="카페 신메뉴 소개",
        summary="바리스타가 카운터에서 오늘의 메뉴를 들어 보이고 가까이 보여 준 뒤 인사한다",
        source=cafe_source(origin),
        format=FormatSpec(
            slots=(
                SceneSlot("greeting", 4000, "바리스타가 카메라를 보며 인사한다", ("barista",)),
                SceneSlot("show", 4000, "바리스타가 오늘의 메뉴를 들어 보인다", ("barista", "mug")),
                SceneSlot(
                    "closeup",
                    4000,
                    "카운터 위의 메뉴를 가까이 보여 준다",
                    ("mug",),
                    (Condition("camera", "close up"),),
                ),
                SceneSlot("outro", 4000, "바리스타가 손을 흔들며 마무리한다", ("barista",)),
            ),
            aspect_ratio="9:16",
            observed_shot_ms=(4000, 4000, 4000, 4000),
        ),
        cast=(
            CastMember(
                "barista",
                EntityKind.PERSON,
                "바리스타",
                role="진행자",
                fixed_attributes=(Condition("outfit", "white shirt and brown apron"),),
            ),
            CastMember(
                "mug",
                EntityKind.PROP,
                "머그컵",
                role="소개하는 메뉴가 담긴 컵",
                fixed_attributes=(Condition("material", "ceramic"),),
            ),
        ),
        style=StyleSpec(
            conditions=(
                Condition("lighting", "warm cafe lighting"),
                Condition("location", "cafe counter"),
            ),
            palette=("warm brown", "cream"),
            notes="인물은 가슴 위로, 배경은 흐리게",
        ),
        text_rules=(
            TextRule(
                "title",
                "오늘의 메뉴",
                TextRole.TITLE,
                slot_keys=("greeting",),
                region=Region(0.1, 0.08, 0.8, 0.08),
                example="오늘의 메뉴",
            ),
            TextRule(
                "price",
                "{menu} {price}",
                TextRole.PRICE,
                slot_keys=("closeup",),
                region=Region(0.1, 0.72, 0.8, 0.08),
                start_ms=500,
                end_ms=3500,
                style=TextStyle(color="#ffe08a", size=0.05),
                example="바닐라 라떼 4,500원",
            ),
            TextRule("caption", "{caption}", TextRole.CAPTION, example="어서 오세요!"),
        ),
        narration=NarrationSpec(
            present=True,
            language="ko",
            tone="친근한 존댓말, 밝고 빠르게",
            pace_cps=5.5,
            beats=(
                NarrationBeat(
                    "greeting", "인사하고 오늘의 메뉴를 예고한다", "안녕하세요, 오늘의 메뉴예요."
                ),
                NarrationBeat("show", "메뉴 이름을 말한다", "바닐라 라떼입니다."),
                NarrationBeat("closeup", "가격을 말한다", "가격은 4,500원."),
                NarrationBeat("outro", "방문을 권하며 인사한다", "매장에서 만나요!"),
            ),
        ),
        music=MusicSpec(present=True, mood="밝은 어쿠스틱"),
        variables=(
            ThemeVariable("menu", "소개하는 메뉴 이름", "바닐라 라떼"),
            ThemeVariable("price", "메뉴 가격", "4,500원"),
            ThemeVariable("caption", "자리마다 넣는 자막", "어서 오세요!", VariableScope.SLOT),
        ),
    )


def matcha_brief(**changes) -> EpisodeBrief:
    """같은 테마의 다른 편: 말차 라떼."""
    base = {
        "title": "말차 라떼 편",
        "topic": "초록색 말차 라떼를 소개한다",
        "variables": {"menu": "말차 라떼", "price": "5,000원"},
        "seed": 7,
    }
    return EpisodeBrief(**{**base, **changes})


def matcha_content(**changes) -> EpisodeContent:
    base = {
        "slots": (
            SlotContent(
                "greeting",
                "바리스타가 카운터 뒤에서 카메라를 보며 인사한다",
                narration="안녕하세요, 오늘의 메뉴예요.",
                variables={"caption": "오늘은 초록초록!"},
            ),
            SlotContent(
                "show",
                "바리스타가 말차 라떼가 담긴 컵을 들어 보인다",
                entity_keys=("latte",),
                requirements=(
                    RequirementSpec("show", "holds", "mug", "barista", "mug"),
                    RequirementSpec("show", "color", "green", "latte"),
                ),
                narration="말차 라떼입니다.",
            ),
            SlotContent(
                "closeup",
                "카운터 위에 놓인 말차 라떼 클로즈업",
                entity_keys=("latte",),
                requirements=(RequirementSpec("closeup", "color", "green", "latte"),),
                narration="가격은 오천 원.",
            ),
            SlotContent("outro", "", narration="매장에서 만나요!"),
        ),
        "entities": (EntitySpec("latte", EntityKind.PROP, "말차 라떼"),),
    }
    return EpisodeContent(**{**base, **changes})


def cafe_observation() -> dict:
    """프레임을 읽는 모델이 카페 숏폼(4초 장면 4개)을 보고 낸 응답 (VlmThemeExtractor 의 재생 응답)."""

    def req(predicate, value, entity=None, related=None):
        return {
            "entity_key": entity,
            "related_entity_key": related,
            "predicate": predicate,
            "value": value,
        }

    return {
        "title": "카페 신메뉴 소개",
        "summary": "바리스타가 오늘의 메뉴를 들어 보이고 가까이 보여 준 뒤 인사한다",
        "entities": [
            {
                "entity_key": "Barista",
                "kind": "PERSON",
                "description": "흰 셔츠에 갈색 앞치마를 입은 바리스타",
                "recurring": True,
                "role": "진행자",
                "fixed_attributes": [
                    {"predicate": "outfit", "value": "White shirt and brown apron"},
                    {"predicate": "hair", "value": "dark hair tied back"},
                    {"predicate": "action", "value": "smiles"},  # 고정 속성이 될 수 없다
                ],
            },
            {
                "entity_key": "mug",
                "kind": "PROP",
                "description": "빨간 머그컵",
                "recurring": True,
                "role": "메뉴가 담긴 컵",
                "fixed_attributes": [{"predicate": "material", "value": "ceramic"}],
            },
            {
                "entity_key": "cafe",
                "kind": "BACKGROUND",
                "description": "따뜻한 조명의 카페",
                "recurring": True,
                "role": "배경",
                "fixed_attributes": [],
            },
        ],
        "slots": [
            {
                "slot": "S1",
                "purpose": "진행자가 카메라를 보며 인사한다",
                "description": "바리스타가 카운터 뒤에서 미소 짓는다",
                "entity_keys": ["barista", "cafe"],
                "requirements": [
                    req("expression", "smiling", "barista"),
                    req("location", "behind cafe counter"),
                ],
                "narration_purpose": "인사하고 오늘의 메뉴를 예고한다",
            },
            {
                "slot": "S2",
                "purpose": "진행자가 오늘의 메뉴를 들어 보인다",
                "description": "바리스타가 빨간 머그컵을 들어 보인다",
                "entity_keys": ["barista", "mug"],
                "requirements": [
                    req("holds", "", "barista", "mug"),
                    req("color", "Red", "mug"),
                    req("near", "", "barista", "cafe"),  # 배경과의 관계는 버린다
                    req("color", "blue", "ghost"),  # 없는 객체
                ],
                "narration_purpose": "메뉴 이름을 말한다",
            },
            {
                "slot": "S3",
                "purpose": "오늘의 메뉴를 가까이 보여 준다",
                "description": "카운터 위의 빨간 머그컵 클로즈업",
                "entity_keys": ["mug"],
                "requirements": [req("color", "red", "mug"), req("camera", "close up")],
                "narration_purpose": "가격을 말한다",
            },
            {
                "slot": "S4",
                "purpose": "진행자가 손을 흔들며 마무리한다",
                "description": "바리스타가 손을 흔든다",
                "entity_keys": ["barista"],
                "requirements": [req("action", "waves hand to camera", "barista")],
                "narration_purpose": "방문을 권한다",
            },
        ],
        "style": {
            "conditions": [
                {"predicate": "lighting", "value": "warm cafe lighting"},
                {"predicate": "mood", "value": "cozy"},
            ],
            "palette": ["warm brown", "cream", "red"],
            "notes": "인물은 가슴 위로, 배경은 흐리게",
        },
        "screen_texts": [
            {
                "text": "오늘의 메뉴",
                "slot": "S1",
                "role": "TITLE",
                "source": "OVERLAY",
                "box": {"x": 0.1, "y": 0.08, "w": 0.8, "h": 0.08},
                "style": "white bold on dark band",
            },
            {
                "text": "바닐라 라떼 4,500원",
                "slot": "S3",
                "role": "PRICE",
                "source": "OVERLAY",
                "box": {"x": 0.1, "y": 0.72, "w": 0.8, "h": 0.4},  # 화면 밖으로 나간 박스
                "style": "yellow on dark band",
            },
            {
                "text": "FRAME",
                "slot": "S3",
                "role": "LABEL",
                "source": "IN_SCENE",
                "box": None,
                "style": "",
            },
        ],
        "text_rules": [
            {
                "rule_key": "title",
                "role": "TITLE",
                "template": "오늘의 메뉴",
                "slots": ["S1"],
                "box": {"x": 0.1, "y": 0.08, "w": 0.8, "h": 0.08},
                "color": "white",
                "background": "black",
                "example": "오늘의 메뉴",
            },
            {
                "rule_key": "Price Tag",
                "role": "PRICE",
                "template": "{menu} {Price}",
                "slots": ["S3"],
                "box": {"x": 0.1, "y": 0.72, "w": 0.8, "h": 0.08},
                "color": "#ffe08a",
                "background": "rgba(0,0,0,0.5)",  # 쓸 수 없는 색 표기
                "example": "바닐라 라떼 4,500원",
            },
        ],
        "variables": [
            {
                "key": "menu",
                "description": "소개하는 메뉴 이름",
                "example": "바닐라 라떼",
                "scope": "EPISODE",
            },
        ],
        "narration_tone": "친근한 존댓말, 밝고 빠르게",
        "music": {"present": None, "mood": ""},
    }


def matcha_content_document() -> dict:
    """기획 모델이 말차 라떼 편에 대해 낸 응답 (LLMSeriesPlanner 의 재생 응답)."""

    def req(predicate, value, entity=None, related=None):
        return {
            "entity_key": entity,
            "related_entity_key": related,
            "predicate": predicate,
            "value": value,
        }

    return {
        "variables": [{"key": "price", "value": "5,000원"}, {"key": "menu", "value": "녹차 라떼"}],
        "entities": [{"entity_key": "latte", "kind": "PROP", "description": "말차 라떼"}],
        "slots": [
            {
                "slot_key": "greeting",
                "description": "바리스타가 카운터 뒤에서 카메라를 보며 인사한다",
                "entity_keys": [],
                "requirements": [req("expression", "smiling", "barista")],
                "narration": "안녕하세요, 오늘의 메뉴예요.",
                "variables": [{"key": "caption", "value": "오늘은 초록초록!"}],
            },
            {
                "slot_key": "show",
                "description": "바리스타가 말차 라떼가 담긴 컵을 들어 보인다",
                "entity_keys": ["latte"],
                "requirements": [
                    req("holds", "mug", "barista", "mug"),
                    req("color", "green", "latte"),
                ],
                "narration": "말차 라떼입니다.",
                "variables": [],
            },
            {
                "slot_key": "closeup",
                "description": "카운터 위에 놓인 말차 라떼 클로즈업",
                "entity_keys": ["latte"],
                "requirements": [
                    req("color", "green", "latte", "mug")
                ],  # 속성에 붙은 상대 객체는 뗀다
                "narration": "가격은 오천 원.",
                "variables": [],
            },
            {
                "slot_key": "outro",
                "description": "바리스타가 손을 흔들며 인사한다",
                "entity_keys": [],
                "requirements": [],
                "narration": "매장에서 만나요!",
                "variables": [],
            },
        ],
    }
