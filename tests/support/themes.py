"""테마·편 테스트 공용 자료: 카페 숏폼(대조군 c3 와 같은 구성)에서 뽑았다고 본 테마."""

from __future__ import annotations

from frame.domain.enums import EntityKind
from frame.domain.screen import Region, TextRole
from frame.domain.shortform import MediaInfo
from frame.domain.theme import (
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
