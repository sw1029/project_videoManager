"""VlmThemeExtractor: 모델의 응답을 테마·원본 IR 로 다듬는 규칙과, 프레임·질의·재시도 (재생 응답, 과금 없음)."""

from __future__ import annotations

import copy
import json

import pytest

from frame.adapters.analysis.vlm_theme import (
    RESPONSE_SCHEMA,
    VlmThemeExtractor,
    build_system_prompt,
    build_theme,
)
from frame.adapters.llm import ReplayLLMClient
from frame.application.errors import LLMTimeout
from frame.application.ports.video_analysis import ThemeExtractionError, ThemeExtractionRequest
from frame.domain.enums import EntityKind
from frame.domain.errors import ValidationError
from frame.domain.screen import Region, TextRole, TextSource
from frame.domain.theme import SourceOrigin, VariableScope
from frame.domain.video_analysis import (
    SpeechSegment,
    Transcript,
    VideoAnalysis,
    shots_from_cuts,
)
from tests.support.media import make_cut_clip
from tests.support.themes import cafe_observation, cafe_source
from tests.support.video import find_ffmpeg

FFMPEG = find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")
SPANS = ((0, 4000), (4000, 8000), (8000, 12000), (12000, 16000))
SAID = Transcript(
    "ko",
    (
        SpeechSegment(400, 2600, "안녕하세요, 오늘의 메뉴예요."),
        SpeechSegment(4500, 6200, "바닐라 라떼입니다."),
        SpeechSegment(8600, 10400, "가격은 4,500원."),
        SpeechSegment(12500, 14000, "매장에서 만나요!"),
    ),
)


def _request(
    *, transcript=SAID, title="", origin=SourceOrigin.EXTERNAL, video=b""
) -> ThemeExtractionRequest:
    source = cafe_source(origin)
    analysis = VideoAnalysis(source.media, shots_from_cuts([4000, 8000, 12000], 16000), transcript)
    return ThemeExtractionRequest("thm_1", source, video, analysis, title=title)


def _build(document=None, **request):
    return build_theme(_request(**request), SPANS, document or cafe_observation())


# ---- 응답 다듬기 ----
def test_observation_becomes_a_valid_theme_and_source_ir():
    theme, ir, texts, notes = _build()

    theme.validate()
    ir.validate()
    assert theme.title == "카페 신메뉴 소개" and theme.theme_id == "thm_1"
    assert [s.slot_key for s in theme.format.slots] == ["slot_1", "slot_2", "slot_3", "slot_4"]
    assert [s.scene_key for s in ir.scenes] == ["slot_1", "slot_2", "slot_3", "slot_4"]
    assert [(s.planned_start_ms, s.planned_end_ms) for s in ir.scenes] == list(SPANS)
    assert theme.format.observed_shot_ms == (4000, 4000, 4000, 4000)
    assert theme.format.aspect_ratio == "9:16"
    assert theme.style.palette == ("warm brown", "cream", "red")
    assert len(texts) == 3 and notes


def test_slot_keys_and_times_come_from_the_code_not_the_model():
    """모델이 자리를 순서 없이, 이름을 다르게 답해도 자리 번호로 맞춘다."""
    document = cafe_observation()
    document["slots"] = [document["slots"][i] for i in (2, 0, 3, 1)]
    document["slots"][0]["slot"] = "slot 3 (closeup)"
    theme, ir, _, _ = _build(document)

    assert [s.purpose for s in theme.format.slots] == [
        "진행자가 카메라를 보며 인사한다",
        "진행자가 오늘의 메뉴를 들어 보인다",
        "오늘의 메뉴를 가까이 보여 준다",
        "진행자가 손을 흔들며 마무리한다",
    ]
    assert ir.scene("slot_3").description == "카운터 위의 빨간 머그컵 클로즈업"


def test_missing_slot_answer_is_rejected():
    document = cafe_observation()
    document["slots"] = document["slots"][:3]

    with pytest.raises(ValidationError, match="S4"):
        _build(document)


def test_cast_is_the_recurring_entities_with_cleaned_fixed_attributes():
    theme, ir, _, notes = _build()

    assert [(c.entity_key, c.kind, c.role) for c in theme.cast] == [
        ("barista", EntityKind.PERSON, "진행자"),
        ("mug", EntityKind.PROP, "메뉴가 담긴 컵"),
    ]
    barista = theme.cast_member("barista")
    # 키는 소문자로, 값은 어휘 규칙으로 정규화되고, 행동은 고정 속성에서 빠진다
    assert [(c.predicate, c.value) for c in barista.fixed_attributes] == [
        ("outfit", "white shirt and brown apron"),
        ("hair", "dark hair tied back"),
    ]
    assert any("action" in note and "고정 속성" in note for note in notes)
    # 배경은 고정 출연이 아니다 (장소는 장면 조건으로 담는다). 원본 IR 의 객체로는 남는다
    assert not theme.has_cast("cafe") and ir.has_entity("cafe")
    assert theme.references() == ()  # 기준 이미지는 붙이지 않는다
    assert [s.cast_keys for s in theme.format.slots] == [
        ("barista",),
        ("barista", "mug"),
        ("mug",),
        ("barista",),
    ]


def test_source_ir_keeps_only_requirements_that_follow_the_ir_rules():
    _, ir, _, notes = _build()
    show = {r.key: r.expected_value for r in ir.scene("slot_2").requirements}

    assert show["slot_2:barista:holds:mug"] == "mug"  # 관계의 값은 상대 객체
    assert show["slot_2:mug:color"] == "red"
    assert "slot_2:barista:near:cafe" not in show  # 배경과의 관계
    assert not any("ghost" in key for key in show)  # 없는 객체
    assert any("관계 조건 'near'" in note for note in notes)
    assert any("없는 객체" in note for note in notes)
    # 고정 출연의 고정 속성과 스타일 조건은 모델이 빠뜨려도 그 장면의 요구조건이다
    assert show["slot_2:barista:outfit"] == "white shirt and brown apron"
    assert show["slot_2:mug:material"] == "ceramic"
    assert show["slot_2:-:lighting"] == "warm cafe lighting" and show["slot_2:-:mood"] == "cozy"


def test_style_and_slot_conditions_are_split():
    theme, _, _, _ = _build()

    assert [(c.predicate, c.value) for c in theme.style.conditions] == [
        ("lighting", "warm cafe lighting"),
        ("mood", "cozy"),
    ]
    by_slot = {
        s.slot_key: [(c.predicate, c.value) for c in s.conditions] for s in theme.format.slots
    }
    assert by_slot == {
        "slot_1": [("location", "behind cafe counter")],
        "slot_2": [],
        "slot_3": [("camera", "close up")],
        "slot_4": [],
    }


def test_text_rules_get_clean_keys_regions_colors_and_declared_variables():
    theme, _, _, notes = _build()
    title, price = theme.text_rules

    assert (title.rule_key, title.template, title.role, title.slot_keys) == (
        "title",
        "오늘의 메뉴",
        TextRole.TITLE,
        ("slot_1",),
    )
    assert title.region == Region(0.1, 0.08, 0.8, 0.08)
    assert (title.style.color, title.style.background) == ("white", "black")
    assert (price.rule_key, price.template, price.slot_keys) == (
        "price_tag",
        "{menu} {price}",
        ("slot_3",),
    )
    assert (
        price.style.color == "#ffe08a" and price.style.background == "black"
    )  # 쓸 수 없는 색은 기본값
    assert price.example == "바닐라 라떼 4,500원"
    # 틀이 쓰는데 선언되지 않은 변수는 테마의 변수로 더한다
    assert [(v.key, v.scope) for v in theme.variables] == [
        ("menu", VariableScope.EPISODE),
        ("price", VariableScope.EPISODE),
    ]
    assert any("'price'" in note for note in notes)


def test_screen_texts_keep_what_was_read_with_time_and_region():
    _, _, texts, _ = _build()

    assert [(t.text, t.start_ms, t.end_ms, t.role, t.source) for t in texts] == [
        ("오늘의 메뉴", 0, 4000, TextRole.TITLE, TextSource.OVERLAY),
        ("바닐라 라떼 4,500원", 8000, 12000, TextRole.PRICE, TextSource.OVERLAY),
        ("FRAME", 8000, 12000, TextRole.LABEL, TextSource.IN_SCENE),
    ]
    cut = texts[1].region  # 화면 밖으로 나간 박스는 화면 안으로 잘린다
    assert (cut.x, cut.y, cut.w) == (0.1, 0.72, 0.8) and cut.h == pytest.approx(0.28)
    assert texts[2].region is None


def test_narration_comes_from_the_transcript_not_from_the_model():
    theme, _, _, _ = _build()
    narration = theme.narration

    assert narration.present and narration.language == "ko"
    assert narration.pace_cps == pytest.approx(SAID.pace_cps)
    assert narration.tone == "친근한 존댓말, 밝고 빠르게"
    assert [(b.slot_key, b.purpose, b.example) for b in narration.beats] == [
        ("slot_1", "인사하고 오늘의 메뉴를 예고한다", "안녕하세요, 오늘의 메뉴예요."),
        ("slot_2", "메뉴 이름을 말한다", "바닐라 라떼입니다."),
        ("slot_3", "가격을 말한다", "가격은 4,500원."),
        ("slot_4", "방문을 권한다", "매장에서 만나요!"),
    ]
    assert theme.music.present is None  # 프레임만으로는 음악을 알 수 없다


def test_silent_video_has_no_narration_even_if_the_model_describes_one():
    theme, _, _, _ = _build(transcript=None)

    assert not theme.narration.present and theme.narration.beats == ()
    assert theme.narration.tone == "" and theme.narration.pace_cps is None


def test_user_title_wins_and_unused_entities_are_dropped():
    document = cafe_observation()
    document["entities"].append(
        {
            "entity_key": "straw",
            "kind": "PROP",
            "description": "빨대",
            "recurring": True,
            "role": "",
            "fixed_attributes": [],
        }
    )
    theme, ir, _, notes = _build(document, title="내가 붙인 제목")

    assert theme.title == "내가 붙인 제목"
    assert not ir.has_entity("straw") and not theme.has_cast("straw")
    assert any("straw" in note for note in notes)


def test_duplicate_or_odd_keys_do_not_break_the_theme():
    document = cafe_observation()
    document["entities"].append(dict(document["entities"][1]))  # mug 가 두 번
    document["entities"].append(
        {
            "entity_key": "2nd Cup!",
            "kind": "thing",  # 알 수 없는 종류
            "description": "",
            "recurring": False,
            "role": "",
            "fixed_attributes": [],
        }
    )
    document["slots"][2]["entity_keys"].append("2nd Cup!")
    theme, ir, _, notes = _build(document)

    assert [e.entity_key for e in ir.entities] == ["barista", "mug", "cafe", "entity_2nd_cup"]
    assert ir.entity("entity_2nd_cup").kind is EntityKind.OTHER
    assert any("두 번" in note for note in notes)
    theme.validate()


def test_looks_are_stripped_from_entity_keys():
    """모델이 키에 색을 넣어 오면(red_mug) 걷어낸다. 조건이 가리키는 키도 함께 바뀐다."""
    document = cafe_observation()
    document["entities"][1]["entity_key"] = "red_mug"
    for slot in document["slots"]:
        slot["entity_keys"] = ["red_mug" if k == "mug" else k for k in slot["entity_keys"]]
        for requirement in slot["requirements"]:
            for field in ("entity_key", "related_entity_key"):
                if requirement[field] == "mug":
                    requirement[field] = "red_mug"
    theme, ir, _, notes = _build(document)

    assert [e.entity_key for e in ir.entities] == ["barista", "mug", "cafe"]
    assert theme.has_cast("mug") and not ir.has_entity("red_mug")
    show = {r.key for r in ir.scene("slot_2").requirements}
    assert {"slot_2:barista:holds:mug", "slot_2:mug:color"} <= show
    assert any("'red_mug' 를 'mug' 로" in note for note in notes)


def test_look_word_is_kept_when_stripping_would_collide_or_leave_nothing():
    document = cafe_observation()
    document["entities"].append(
        {
            "entity_key": "blue_mug",  # mug 가 이미 있다
            "kind": "PROP",
            "description": "파란 머그컵",
            "recurring": False,
            "role": "",
            "fixed_attributes": [],
        }
    )
    document["entities"].append(
        {
            "entity_key": "white",  # 걷어내면 남는 것이 없다
            "kind": "OTHER",
            "description": "흰 것",
            "recurring": False,
            "role": "",
            "fixed_attributes": [],
        }
    )
    document["slots"][2]["entity_keys"] += ["blue_mug", "white"]
    _, ir, _, _ = _build(document)

    assert [e.entity_key for e in ir.entities] == ["barista", "mug", "cafe", "blue_mug", "white"]


def test_relation_to_something_that_is_not_an_entity_becomes_an_action():
    """'카메라를 본다' 처럼 상대가 객체가 아닌 관계는 버리지 않고 그 객체의 행동으로 옮긴다."""
    document = cafe_observation()
    document["slots"][0]["requirements"].append(
        {
            "entity_key": "barista",
            "related_entity_key": "camera",
            "predicate": "looks_at",
            "value": "",
        }
    )
    document["slots"][3]["requirements"].append(  # 이미 행동이 있으면 앞의 것이 남는다
        {
            "entity_key": "barista",
            "related_entity_key": None,
            "predicate": "looks_at",
            "value": "viewer",
        }
    )
    document["slots"][1]["requirements"].append(  # 상대도 값도 없으면 버린다
        {"entity_key": "barista", "related_entity_key": None, "predicate": "wears", "value": ""}
    )
    _, ir, _, notes = _build(document)
    greeting = {r.key: r.expected_value for r in ir.scene("slot_1").requirements}
    outro = {r.key: r.expected_value for r in ir.scene("slot_4").requirements}

    assert greeting["slot_1:barista:action"] == "looks at camera"
    assert outro["slot_4:barista:action"] == "waves hand to camera"
    assert not any("looks_at" in key for key in (*greeting, *outro))
    assert any("'wears'" in note and "상대 없음" in note for note in notes)


def test_predicates_outside_the_vocabulary_are_dropped():
    document = cafe_observation()
    document["slots"][2]["requirements"] += [
        {
            "entity_key": "mug",
            "related_entity_key": None,
            "predicate": "close_up",
            "value": "mug centered",
        },
        {
            "entity_key": "cafe",
            "related_entity_key": None,
            "predicate": "blurred",
            "value": "background blur",
        },
    ]
    _, ir, _, notes = _build(document)
    predicates = {r.predicate for r in ir.scene("slot_3").requirements}

    assert not {"close_up", "blurred"} & predicates
    assert sum("어휘에 없는 술어" in note for note in notes) == 2


# ---- 프레임·질의·재시도 (진짜 ffmpeg, 재생 응답) ----
@pytest.fixture(scope="module")
def video(tmp_path_factory) -> bytes:
    folder = tmp_path_factory.mktemp("theme")
    return make_cut_clip(
        FFMPEG, folder / "cafe.mp4", colors=("red", "blue", "green", "yellow"), seconds_each=4.0
    )


def _extractor(*responses, **options) -> tuple[VlmThemeExtractor, ReplayLLMClient]:
    llm = ReplayLLMClient(
        [r if isinstance(r, str | Exception) else json.dumps(r) for r in responses]
    )
    return VlmThemeExtractor(FFMPEG, llm, **options), llm


@needs_ffmpeg
def test_frames_are_sampled_per_slot_and_described_in_the_prompt(video):
    extractor, llm = _extractor(cafe_observation())
    extraction = extractor.extract(_request(video=video, title="카페 신메뉴 소개"))

    call = llm.calls[0]
    assert [image.label for image in call["images"]] == [
        "S1-F1 0.7s",
        "S1-F2 2.0s",
        "S1-F3 3.3s",
        "S2-F1 4.7s",
        "S2-F2 6.0s",
        "S2-F3 7.3s",
        "S3-F1 8.7s",
        "S3-F2 10.0s",
        "S3-F3 11.3s",
        "S4-F1 12.7s",
        "S4-F2 14.0s",
        "S4-F3 15.3s",
    ]
    user = call["user"]
    assert "S3 8.0-12.0s (frames: S3-F1 8.7s, S3-F2 10.0s, S3-F3 11.3s)" in user
    assert "[8.6-10.4] 가격은 4,500원." in user and "Transcript (language: ko)" in user
    assert "480x854 (9:16), 16.0 s, audio: yes" in user
    # 설명은 한국어로, 조건의 값은 영어로 (첫 실호출에서 값까지 한국어로 왔다)
    assert "narration_purpose in Korean" in user and "value in English" in user
    assert "Title given by the user: 카페 신메뉴 소개" in user
    assert call["schema_name"] == "theme_observation" and call["schema"] == RESPONSE_SCHEMA
    assert extraction.detail["frames"] == 12 and extraction.detail["method"] == "vlm_frames"
    assert extraction.detail["attempts"] == 1 and extraction.theme.title == "카페 신메뉴 소개"


@needs_ffmpeg
def test_frame_budget_is_shared_between_slots(video):
    extractor, llm = _extractor(cafe_observation(), frames_per_slot=3, max_frames=4)
    extractor.extract(_request(video=video, transcript=None))

    assert [image.label.split()[0] for image in llm.calls[0]["images"]] == [
        "S1-F1",
        "S2-F1",
        "S3-F1",
        "S4-F1",
    ]
    assert "Transcript: none" in llm.calls[0]["user"]


@needs_ffmpeg
def test_invalid_answer_is_sent_back_once_and_then_fails(video):
    broken = copy.deepcopy(cafe_observation())
    broken["slots"] = broken["slots"][:2]
    extractor, llm = _extractor(broken, cafe_observation(), max_attempts=2)
    extraction = extractor.extract(_request(video=video))

    assert extraction.detail["attempts"] == 2
    assert (
        "Your previous answer was rejected" in llm.calls[1]["user"] and "S3" in llm.calls[1]["user"]
    )
    assert len(llm.calls[1]["images"]) == 12  # 다시 물을 때도 프레임을 함께 보낸다

    failing, _ = _extractor(broken, "not json", max_attempts=2)
    with pytest.raises(ThemeExtractionError, match="2회"):
        failing.extract(_request(video=video))
    timeout, _ = _extractor(LLMTimeout("응답 없음"))
    with pytest.raises(LLMTimeout):
        timeout.extract(_request(video=video))


def test_system_prompt_lists_the_vocabulary_and_the_rules():
    prompt = build_system_prompt()

    assert "outfit" in prompt and "holds" in prompt and "close" in prompt
    assert '"mug", not "red_mug"' in prompt
    assert "{menu} {price}" in prompt  # 틀의 예는 중괄호 그대로 보인다
    assert "ENGLISH phrase" in prompt and "Use only the predicate names" in prompt
    assert "present is null" in prompt


def test_colour_names_written_in_two_words_are_understood():
    """모델은 'light yellow' 처럼 적어 온다. CSS 색 이름으로 맞추고, 띠가 없다는 것(null)은 그대로 둔다."""
    document = cafe_observation()
    document["text_rules"][0]["color"] = "Light Yellow"
    document["text_rules"][0]["background"] = None
    document["text_rules"][1]["color"] = "shiny rainbow"
    theme, _, _, _ = _build(document)
    title, price = theme.text_rules

    assert (title.style.color, title.style.background) == ("lightyellow", None)
    assert price.style.color == "white"  # 모르는 색은 기본색


def test_overlay_text_written_as_an_entity_is_kept_out_of_the_source_ir():
    """얹은 문구는 생성 조건이 아니다. 영상 안에 찍힌 글자(IN_SCENE)만 객체·조건으로 남는다."""
    document = cafe_observation()
    for key, description in (("price_text", "가격 문구"), ("cup_label", "컵에 찍힌 글자")):
        document["entities"].append(
            {
                "entity_key": key,
                "kind": "TEXT",
                "description": description,
                "recurring": False,
                "role": "",
                "fixed_attributes": [],
            }
        )
    document["slots"][2]["entity_keys"] += ["price_text", "cup_label"]

    def req(entity, predicate, value):
        return {
            "entity_key": entity,
            "related_entity_key": None,
            "predicate": predicate,
            "value": value,
        }

    document["slots"][2]["requirements"] += [
        req("price_text", "text", "바닐라 라떼 4,500원"),
        req("price_text", "color", "yellow"),
        req("cup_label", "text", "FRAME"),
    ]
    _, ir, texts, notes = _build(document)

    assert not ir.has_entity("price_text") and ir.has_entity("cup_label")
    closeup = {r.key: r.expected_value for r in ir.scene("slot_3").requirements}
    assert closeup["slot_3:cup_label:text"] == "FRAME"
    assert not any("price_text" in key for key in closeup)
    assert any("price_text" in note and "얹은 문구" in note for note in notes)
    assert len(texts) == 3  # 화면에서 읽은 문구로는 그대로 남는다


def test_camera_is_never_a_style_condition():
    """카메라는 자리마다 다르다. 스타일에 적어 오면 빼서, 모든 장면의 조건이 되지 않게 한다."""
    document = cafe_observation()
    document["style"]["conditions"].append({"predicate": "camera", "value": "static and close up"})
    theme, ir, _, _ = _build(document)

    assert [c.predicate for c in theme.style.conditions] == ["lighting", "mood"]
    cameras = {
        s.scene_key: [r.expected_value for r in s.requirements if r.predicate == "camera"]
        for s in ir.scenes
    }
    assert cameras == {"slot_1": [], "slot_2": [], "slot_3": ["close up"], "slot_4": []}
