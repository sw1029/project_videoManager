"""IdentityReferenceExtractor 계약 테스트 (ADR-0015). 구현은 params 로 추가한다.

- fake: 영상을 읽지 않는다.
- frame / vlm_crop: 실제 ffmpeg 로 합성 클립에서 프레임을 뽑는다 (ffmpeg 가 없으면 건너뜀). VLM 은 재생 클라이언트.
"""

from __future__ import annotations

import json
import subprocess
import threading

import pytest

from frame.adapters.identity import (
    FakeIdentityExtractor,
    FrameIdentityExtractor,
    VlmCropIdentityExtractor,
)
from frame.adapters.llm import ReplayLLMClient
from frame.adapters.shared.media.ffmpeg import VideoToolError
from frame.adapters.shared.media.frames import FrameSampler, extract_frame
from frame.adapters.storage import InMemoryArtifactStore
from frame.application.errors import ConfigurationError
from frame.application.ports.identity import IdentityExtractionRequest
from frame.domain.artifacts import GeneratedClip
from tests.support.video import find_ffmpeg, make_clip

FFMPEG = find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")


def _located(*entity_keys: str) -> str:
    """VLM 이 요청한 객체를 모두 화면 가운데에서 찾았다고 답한다."""
    return json.dumps(
        {
            "entities": [
                {
                    "entity_key": key,
                    "visible": True,
                    "box": {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5},
                    "appearance": f"{key} appearance",
                }
                for key in entity_keys
            ]
        }
    )


def _fake(store):
    return FakeIdentityExtractor(store)


def _frame(store):
    return FrameIdentityExtractor(store, FFMPEG)


def _vlm_crop(store):
    # 호출마다 같은 답을 돌려주도록 넉넉히 넣어 둔다
    llm = ReplayLLMClient([_located("barista", "cup")] * 8)
    return VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8)


EXTRACTORS = [
    pytest.param(_fake, id="fake"),
    pytest.param(_frame, id="frame", marks=needs_ffmpeg),
    pytest.param(_vlm_crop, id="vlm_crop", marks=needs_ffmpeg),
]


@pytest.fixture
def store():
    return InMemoryArtifactStore()


@pytest.fixture(params=EXTRACTORS)
def extractor(request, store):
    return request.param(store)


@pytest.fixture(scope="module")
def clip_bytes(tmp_path_factory):
    if FFMPEG is None:
        return b"not-a-video"  # fake 추출기는 내용을 읽지 않는다
    return make_clip(FFMPEG, tmp_path_factory.mktemp("clip") / "intro.mp4")


def _request(store, ir, clip_bytes, *entity_keys, clip_key="clips/ver_1/intro.mp4"):
    scene = ir.scene("intro")
    uri = store.put(clip_key, clip_bytes, "video/mp4")
    clip = GeneratedClip("intro", uri, scene.duration_ms, "f" * 64)
    return IdentityExtractionRequest(
        version_id="ver_1",
        scene=scene,
        clip=clip,
        entities=tuple(ir.entity(key) for key in entity_keys),
    )


def test_returns_one_stored_image_per_requested_entity(extractor, store, ir, clip_bytes):
    images = extractor.extract(_request(store, ir, clip_bytes, "barista", "cup"))

    assert [i.entity_key for i in images] == ["barista", "cup"]
    for image in images:
        assert store.exists(image.image_uri)
        assert store.content_type(image.image_uri).startswith("image/")
        assert store.get(image.image_uri)


def test_never_returns_entities_that_were_not_requested(extractor, store, ir, clip_bytes):
    images = extractor.extract(_request(store, ir, clip_bytes, "cup"))
    assert [i.entity_key for i in images] == ["cup"]
    assert extractor.extract(_request(store, ir, clip_bytes)) == ()


def test_same_clip_and_entity_give_the_same_uri(extractor, store, ir, clip_bytes):
    first = extractor.extract(_request(store, ir, clip_bytes, "barista"))
    second = extractor.extract(_request(store, ir, clip_bytes, "barista"))
    assert [i.image_uri for i in first] == [i.image_uri for i in second]


@needs_ffmpeg
def test_a_different_crop_of_the_same_clip_gets_a_different_uri(store, ir, clip_bytes):
    """URI 가 같으면 내용도 같아야 한다 (생성 캐시·원장 키가 기준 이미지 URI 로 만들어진다)."""

    def crop(box):
        answer = json.loads(_located("barista"))
        answer["entities"][0]["box"] = box
        llm = ReplayLLMClient([json.dumps(answer)])
        extractor = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8, reuse_existing=False)
        return extractor.extract(_request(store, ir, clip_bytes, "barista"))[0].image_uri

    left = crop({"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0})
    right = crop({"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0})
    assert left != right
    assert crop({"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}) == left


def test_different_clips_do_not_share_an_identity_image(extractor, store, ir, clip_bytes):
    """조건 fingerprint 가 같아도 클립이 다르면 기준 이미지를 덮어쓰지 않는다."""
    one = extractor.extract(_request(store, ir, clip_bytes, "barista"))
    other = extractor.extract(
        _request(store, ir, clip_bytes, "barista", clip_key="clips/ver_2/intro.mp4")
    )
    assert one[0].image_uri != other[0].image_uri


# ---- 구현별 동작 ----------------------------------------------------------------------


def test_fake_can_miss_entities_and_fail(store, ir, clip_bytes):
    missing = FakeIdentityExtractor(store, missing_entity_keys={"cup"})
    assert [
        i.entity_key for i in missing.extract(_request(store, ir, clip_bytes, "barista", "cup"))
    ] == ["barista"]
    broken = FakeIdentityExtractor(store, error=RuntimeError("고장"))
    with pytest.raises(RuntimeError):
        broken.extract(_request(store, ir, clip_bytes, "cup"))


@needs_ffmpeg
def test_frame_extractor_shares_one_full_frame_between_entities(store, ir, clip_bytes):
    images = FrameIdentityExtractor(store, FFMPEG, min_output_side=64).extract(
        _request(store, ir, clip_bytes, "barista", "cup")
    )

    assert len({i.image_uri for i in images}) == 1  # 화면 전체 한 장을 함께 쓴다
    assert _size(store.get(images[0].image_uri)) == (64, 114)
    assert images[0].detail["frame_ms"] == 2000  # 4초 클립의 가운데


@needs_ffmpeg
def test_small_identity_images_are_enlarged_to_the_minimum_reference_size(store, ir, clip_bytes):
    """영상 모델은 참조 이미지에 최소 크기를 요구한다 (Wan: 가로·세로 240 px 이상)."""
    (frame,) = FrameIdentityExtractor(store, FFMPEG).extract(
        _request(store, ir, clip_bytes, "barista")
    )
    llm = ReplayLLMClient([_located("cup")])
    (crop,) = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8).extract(
        _request(store, ir, clip_bytes, "cup")
    )

    for image in (frame, crop):
        width, height = _size(store.get(image.image_uri))
        assert min(width, height) == 480
    assert crop.detail["native_size"][0] < 64  # 잘라 낸 원본은 작았다


@needs_ffmpeg
def test_vlm_crop_skips_subjects_that_are_too_small_in_the_frame(store, ir, clip_bytes):
    llm = ReplayLLMClient([_located("cup"), _located("cup")])
    # 64x114 프레임의 절반 크기 박스 -> 약 40x70 px: 기본 하한(96 px)보다 작다
    assert (
        VlmCropIdentityExtractor(store, FFMPEG, llm).extract(_request(store, ir, clip_bytes, "cup"))
        == ()
    )


@needs_ffmpeg
def test_vlm_crop_cuts_each_entity_out_of_the_frame(store, ir, clip_bytes):
    hidden_cup = {"entity_key": "cup", "visible": False, "box": None, "appearance": ""}
    first = {
        "entities": [
            {
                "entity_key": "barista",
                "visible": True,
                "box": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5},
                "appearance": "short black hair, white shirt",
            },
            hidden_cup,
        ]
    }
    llm = ReplayLLMClient([json.dumps(first), json.dumps({"entities": [hidden_cup]})])
    extractor = VlmCropIdentityExtractor(
        store, FFMPEG, llm, padding=0.0, min_side=8, min_output_side=8, person_max_aspect=0
    )

    images = extractor.extract(_request(store, ir, clip_bytes, "barista", "cup"))

    assert [i.entity_key for i in images] == ["barista"]  # 보이지 않는 컵은 뺀다
    assert _size(store.get(images[0].image_uri)) == (32, 56)  # 64x114 의 왼쪽 위 1/4
    assert images[0].description == "short black hair, white shirt"
    assert images[0].detail["box"] == {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5}
    first_call, second_call = llm.calls
    assert len(first_call["images"]) == 1 and first_call["images"][0].media_type == "image/jpeg"
    assert "barista" in first_call["user"] and "cup" in first_call["user"]
    assert "barista" not in second_call["user"]  # 못 찾은 객체만 다른 프레임에서 다시 본다


@needs_ffmpeg
def test_vlm_crop_keeps_what_it_found_when_a_later_look_fails(store, ir, clip_bytes):
    answer = json.loads(_located("barista"))
    answer["entities"].append(
        {"entity_key": "cup", "visible": False, "box": None, "appearance": ""}
    )
    llm = ReplayLLMClient([json.dumps(answer)])  # 두 번째 프레임을 볼 때는 응답이 없다 (호출 실패)

    images = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8).extract(
        _request(store, ir, clip_bytes, "barista", "cup")
    )

    assert [i.entity_key for i in images] == ["barista"]


@needs_ffmpeg
def test_vlm_crop_does_not_extract_the_same_clip_and_entity_twice(store, ir, clip_bytes):
    """VLM 이 짚는 박스는 호출마다 달라질 수 있다. 한 번 뽑은 기준을 다시 쓰면 생성 키가 흔들리지 않는다."""
    first_answer = json.loads(_located("barista"))
    other_answer = json.loads(_located("barista", "cup"))
    other_answer["entities"][0]["box"] = {"x": 0.0, "y": 0.0, "w": 0.4, "h": 0.4}
    llm = ReplayLLMClient([json.dumps(first_answer), json.dumps(other_answer)])
    extractor = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8)

    (first,) = extractor.extract(_request(store, ir, clip_bytes, "barista"))
    again, cup = extractor.extract(_request(store, ir, clip_bytes, "barista", "cup"))

    assert again.image_uri == first.image_uri and again.description == first.description
    assert again.detail["reused"] is True and "reused" not in cup.detail
    assert len(llm.calls) == 2
    assert "barista" not in llm.calls[1]["user"]  # 이미 있는 객체는 다시 묻지 않는다
    # 자르는 설정을 바꾸면 예전 기준을 쓰지 않고 다시 뽑는다
    tighter = VlmCropIdentityExtractor(
        store,
        FFMPEG,
        ReplayLLMClient([json.dumps(first_answer)]),
        min_side=8,
        person_max_aspect=0.8,
    )
    (recut,) = tighter.extract(_request(store, ir, clip_bytes, "barista"))
    assert recut.image_uri != first.image_uri and "reused" not in recut.detail
    # 다른 클립은 색인이 따로다
    (other,) = VlmCropIdentityExtractor(
        store, FFMPEG, ReplayLLMClient([json.dumps(first_answer)]), min_side=8
    ).extract(_request(store, ir, clip_bytes, "barista", clip_key="clips/ver_2/intro.mp4"))
    assert other.image_uri != first.image_uri


@needs_ffmpeg
def test_vlm_crop_keeps_only_the_head_and_shoulders_of_a_person(store, ir, clip_bytes):
    """VLM 이 상반신까지 내려오는 박스를 줘도 인물은 위쪽만 쓴다 (손에 든 물건이 기준에 들어가지 않게)."""
    tall = {"x": 0.25, "y": 0.1, "w": 0.5, "h": 0.8}  # 64x114 프레임에서 32x91 px
    answer = json.loads(_located("barista", "cup"))
    for entity in answer["entities"]:
        entity["box"] = tall
    llm = ReplayLLMClient([json.dumps(answer)])
    extractor = VlmCropIdentityExtractor(
        store, FFMPEG, llm, padding=0.0, min_side=8, min_output_side=8, person_max_aspect=1.25
    )

    person, prop = extractor.extract(_request(store, ir, clip_bytes, "barista", "cup"))

    assert _size(store.get(person.image_uri)) == (32, 40)  # 세로 = 가로 x 1.25
    assert person.detail["region"]["y"] == 0.1  # 박스의 위쪽부터
    assert _size(store.get(prop.image_uri)) == (32, 90)  # 소품은 박스 그대로


@needs_ffmpeg
def test_vlm_crop_tries_a_second_frame_for_entities_it_could_not_see(store, ir, clip_bytes):
    hidden = json.dumps(
        {"entities": [{"entity_key": "cup", "visible": False, "box": None, "appearance": ""}]}
    )
    llm = ReplayLLMClient([hidden, _located("cup")])

    images = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8).extract(
        _request(store, ir, clip_bytes, "cup")
    )

    assert [i.entity_key for i in images] == ["cup"]
    assert len(llm.calls) == 2
    assert images[0].detail["frame_ms"] != 2000  # 두 번째로 본 프레임


@needs_ffmpeg
def test_vlm_crop_ignores_boxes_that_are_too_small_or_malformed(store, ir, clip_bytes):
    answer = json.dumps(
        {
            "entities": [
                {
                    "entity_key": "barista",
                    "visible": True,
                    "box": {"x": 0.4, "y": 0.4, "w": 0.01, "h": 0.01},
                    "appearance": "",
                },
                {
                    "entity_key": "cup",
                    "visible": True,
                    "box": {"x": 2.0, "y": 0.1, "w": -1, "h": 0.2},
                    "appearance": "",
                },
            ]
        }
    )
    llm = ReplayLLMClient([answer, answer])

    assert (
        VlmCropIdentityExtractor(store, FFMPEG, llm).extract(
            _request(store, ir, clip_bytes, "barista", "cup")
        )
        == ()
    )


# ---- 고정 속성 확인: 잘못 나온 객체는 기준으로 쓰지 않는다 -------------------------------------


def _judged(key: str, verdicts: dict[str, str], *, visible: bool = True) -> dict:
    """VLM 이 객체를 화면 가운데에서 찾고, 확인할 고정 속성마다 판정을 붙인 답."""
    return {
        "entity_key": key,
        "visible": visible,
        "box": {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5} if visible else None,
        "appearance": f"{key} appearance",
        "attributes": [{"name": name, "verdict": verdict} for name, verdict in verdicts.items()],
    }


def _answer(*entities: dict) -> str:
    return json.dumps({"entities": list(entities)})


@needs_ffmpeg
def test_vlm_crop_does_not_use_a_subject_that_contradicts_its_fixed_attributes(
    store, ir, clip_bytes, caplog
):
    """클립이 요구(컵 색)를 반영하지 못했으면 그 컵을 기준으로 삼지 않는다. 다음 장면으로 번지지 않게 한다.

    2026-11-12 실호출: 청록으로 바꾸라는 수정에서 겉이 빨간 컵이 나왔는데 그 컵이 기준 이미지로 저장됐다.
    """
    llm = ReplayLLMClient([_answer(_judged("cup", {"color: red": "no"}))])
    extractor = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8)

    with caplog.at_level("WARNING"):
        images = extractor.extract(_request(store, ir, clip_bytes, "cup"))

    assert images == ()
    assert len(llm.calls) == 1  # 다른 프레임을 다시 보지 않는다 (그 클립의 객체가 그 모습이다)
    assert "cup" in caplog.text and "color: red" in caplog.text


@needs_ffmpeg
def test_vlm_crop_remembers_that_a_clip_was_rejected(store, ir, clip_bytes):
    """같은 클립을 다시 물으면 VLM 답이 흔들려 이번에는 기준이 될 수 있다. 한 번 거른 클립은 다시 묻지 않는다."""
    llm = ReplayLLMClient([_answer(_judged("cup", {"color: red": "no"})), _located("cup")])
    extractor = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8)

    assert extractor.extract(_request(store, ir, clip_bytes, "cup")) == ()
    assert extractor.extract(_request(store, ir, clip_bytes, "cup")) == ()
    assert len(llm.calls) == 1
    # 다른 클립은 따로 본다
    (other,) = extractor.extract(
        _request(store, ir, clip_bytes, "cup", clip_key="clips/ver_2/intro.mp4")
    )
    assert other.entity_key == "cup" and len(llm.calls) == 2


@needs_ffmpeg
@pytest.mark.parametrize("verdict", ["yes", "unclear"])
def test_vlm_crop_uses_a_subject_whose_attributes_hold_or_cannot_be_judged(
    store, ir, clip_bytes, verdict
):
    """분명히 다를 때만 거른다. 이 프레임에서 판단할 수 없는 속성 때문에 기준을 버리지 않는다."""
    llm = ReplayLLMClient([_answer(_judged("cup", {"color: red": verdict}))])

    (image,) = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8).extract(
        _request(store, ir, clip_bytes, "cup")
    )

    assert store.exists(image.image_uri)
    assert image.detail["attributes"] == {"color: red": verdict}


@needs_ffmpeg
def test_vlm_crop_judges_each_subject_on_its_own(store, ir, clip_bytes):
    both = _answer(
        _judged("barista", {"shirt color: white": "yes"}),
        _judged("cup", {"color: red": "no"}),
    )
    llm = ReplayLLMClient([both])

    images = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8).extract(
        _request(store, ir, clip_bytes, "barista", "cup")
    )

    assert [i.entity_key for i in images] == ["barista"]
    assert len(llm.calls) == 1


@needs_ffmpeg
def test_vlm_crop_only_counts_verdicts_on_the_attributes_it_asked_about(store, ir, clip_bytes):
    """모델이 묻지 않은 것(행동 같은 일시적 조건)에 'no' 를 붙여도 기준을 버리지 않는다.

    이름의 대소문자·공백·구두점 차이는 맞춰 읽는다.
    """
    judged = _judged("cup", {"action: shows cup to camera": "no", "Color : RED": "yes"})
    llm = ReplayLLMClient([_answer(judged)])

    (image,) = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8).extract(
        _request(store, ir, clip_bytes, "cup")
    )

    assert image.detail["attributes"] == {"color: red": "yes"}
    respelled = ReplayLLMClient([_answer(_judged("cup", {"COLOR:red": "no"}))])
    assert (
        VlmCropIdentityExtractor(store, FFMPEG, respelled, min_side=8).extract(
            _request(store, ir, clip_bytes, "cup", clip_key="clips/ver_2/intro.mp4")
        )
        == ()
    )


@needs_ffmpeg
def test_vlm_crop_reads_an_answer_whose_key_repeats_the_whole_subject_line(store, ir, clip_bytes):
    """실제 모델(gpt-4.1)이 entity_key 에 객체 키 대신 질의의 한 줄을 통째로 돌려줬다 (2026-11-12 실호출).

    그 답을 못 읽으면 '찾지 못함' 이 되어 한 번 더 묻고, 거절도 기록되지 않는다.
    """
    wrong = _judged("cup", {"color: red": "no"})
    wrong["entity_key"] = "cup (prop): 세라믹 머그컵; color: red"
    llm = ReplayLLMClient([_answer(wrong)])
    extractor = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8)

    assert extractor.extract(_request(store, ir, clip_bytes, "cup")) == ()
    assert len(llm.calls) == 1  # 거절로 읽었다: 다른 프레임을 다시 보지 않는다
    assert extractor.extract(_request(store, ir, clip_bytes, "cup")) == ()
    assert len(llm.calls) == 1  # 거절이 기록됐다: 다시 묻지 않는다

    fine = _judged("barista", {"shirt color: white": "yes"})
    fine["entity_key"] = "- barista (person): 흰 셔츠를 입은 바리스타; shirt color: white"
    (image,) = VlmCropIdentityExtractor(
        store, FFMPEG, ReplayLLMClient([_answer(fine)]), min_side=8
    ).extract(_request(store, ir, clip_bytes, "barista"))
    assert image.entity_key == "barista" and store.exists(image.image_uri)


@needs_ffmpeg
def test_vlm_crop_does_not_mix_up_answers_between_subjects(store, ir, clip_bytes):
    """키가 다른 객체의 답을 끌어다 쓰지 않는다. 하나만 묻고 하나만 받았을 때만 키가 달라도 그 답으로 본다."""
    stranger = _judged("cupboard", {"color: red": "yes"})
    both = ReplayLLMClient([_answer(stranger)] * 2)
    extractor = VlmCropIdentityExtractor(store, FFMPEG, both, min_side=8)

    # 둘을 물었는데 누구의 답인지 알 수 없는 항목 하나: 어느 쪽에도 쓰지 않는다
    assert extractor.extract(_request(store, ir, clip_bytes, "barista", "cup")) == ()

    only = ReplayLLMClient([_answer(stranger)])
    (image,) = VlmCropIdentityExtractor(store, FFMPEG, only, min_side=8).extract(
        _request(store, ir, clip_bytes, "cup", clip_key="clips/ver_2/intro.mp4")
    )
    assert image.entity_key == "cup"


@needs_ffmpeg
def test_vlm_crop_looks_again_when_the_subject_is_not_visible(store, ir, clip_bytes):
    """보이지 않는다는 답은 거절이 아니다. 예전처럼 다른 프레임을 한 번 더 본다."""
    hidden = _answer(_judged("cup", {"color: red": "no"}, visible=False))
    llm = ReplayLLMClient([hidden, _answer(_judged("cup", {"color: red": "yes"}))])

    images = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8).extract(
        _request(store, ir, clip_bytes, "cup")
    )

    assert [i.entity_key for i in images] == ["cup"] and len(llm.calls) == 2


@needs_ffmpeg
def test_vlm_crop_asks_the_model_to_check_the_fixed_attributes(store, ir, clip_bytes):
    llm = ReplayLLMClient([_located("barista", "cup")])
    VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8).extract(
        _request(store, ir, clip_bytes, "barista", "cup")
    )

    (call,) = llm.calls
    lines = call["user"].splitlines()
    assert "  fixed attributes to check: color: red" in lines
    # 관계(holds)는 고정 속성이 아니다
    assert "  fixed attributes to check: shirt color: white" in lines
    item = call["schema"]["properties"]["entities"]["items"]
    assert "attributes" in item["required"]
    verdict = item["properties"]["attributes"]["items"]["properties"]["verdict"]
    assert verdict["enum"] == ["yes", "no", "unclear"]
    assert "unclear" in call["system"]


@needs_ffmpeg
def test_vlm_crop_can_skip_the_attribute_check(store, ir, clip_bytes):
    """끄면 예전 질의 그대로다 (위치와 외형만 묻는다). VLM 이 '다르다' 고 답해도 거르지 않는다."""
    llm = ReplayLLMClient([_answer(_judged("cup", {"color: red": "no"}))])
    extractor = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8, verify_attributes=False)

    (image,) = extractor.extract(_request(store, ir, clip_bytes, "cup"))

    (call,) = llm.calls
    assert "fixed attributes" not in call["user"] and "unclear" not in call["system"]
    assert "attributes" not in call["schema"]["properties"]["entities"]["items"]["properties"]
    assert store.exists(image.image_uri) and "attributes" not in image.detail


@needs_ffmpeg
def test_vlm_crop_does_not_reuse_references_extracted_without_the_check(store, ir, clip_bytes):
    """확인 없이 뽑아 둔 기준은 확인을 켠 추출기가 다시 쓰지 않는다 (색인은 같은 설정의 결과만 쓴다)."""
    unchecked = VlmCropIdentityExtractor(
        store, FFMPEG, ReplayLLMClient([_located("cup")]), min_side=8, verify_attributes=False
    )
    assert len(unchecked.extract(_request(store, ir, clip_bytes, "cup"))) == 1
    llm = ReplayLLMClient([_answer(_judged("cup", {"color: red": "no"}))])

    checked = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8).extract(
        _request(store, ir, clip_bytes, "cup")
    )

    assert checked == () and len(llm.calls) == 1


@needs_ffmpeg
def test_vlm_crop_keeps_the_other_subjects_when_one_cannot_be_cut_out(
    store, ir, clip_bytes, caplog
):
    """객체 하나를 잘라 내지 못해도 (가는 박스에서 ffmpeg 가 실패한다) 같은 프레임에서 찾은 다른 객체의 기준은 돌려준다.

    예전에는 그 오류가 추출 전체를 끝냈다. 이미 뽑은 기준도 돌려주지 않아, 다음에 다시 물으면 다른 박스가 나오고
    기준 이미지(= 생성 캐시·원장의 키)가 달라졌다 (감사 A-27).
    """

    def extract(ffmpeg, video, at_ms, **options):
        crop = options.get("crop")
        if crop is not None and crop.h < 0.05:
            raise VideoToolError("프레임을 뽑지 못했습니다 (가는 박스)")
        return extract_frame(ffmpeg, video, at_ms, **options)

    answer = json.dumps(
        {
            "entities": [
                {
                    "entity_key": "barista",
                    "visible": True,
                    "box": {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5},
                    "appearance": "barista appearance",
                },
                {  # 넓이는 하한을 넘지만 세로가 아주 가는 박스
                    "entity_key": "cup",
                    "visible": True,
                    "box": {"x": 0.05, "y": 0.5, "w": 0.9, "h": 0.01},
                    "appearance": "cup appearance",
                },
            ]
        }
    )
    llm = ReplayLLMClient([answer, answer])
    extractor = VlmCropIdentityExtractor(
        store, FFMPEG, llm, min_side=8, sampler=FrameSampler(FFMPEG, extract=extract)
    )

    with caplog.at_level("WARNING"):
        images = extractor.extract(_request(store, ir, clip_bytes, "barista", "cup"))

    assert [image.entity_key for image in images] == ["barista"]
    assert "cup" in caplog.text and "VIDEO_TOOL_ERROR" in caplog.text


@needs_ffmpeg
def test_vlm_crop_reads_a_verdict_named_by_the_predicate_alone(store, ir, clip_bytes):
    """모델이 속성 이름을 '술어: 값' 이 아니라 술어만으로 돌려줘도 그 속성의 판정으로 읽는다.

    읽지 못하면 'no' 가 버려져, 요구와 다르게 나온 객체가 기준 이미지가 된다 (감사 A-27).
    """
    for index, name in enumerate(("color", "Color", " COLOR: ")):
        llm = ReplayLLMClient([_answer(_judged("cup", {name: "no"}))])
        extractor = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8)
        key = f"clips/ver_named_{index}/intro.mp4"

        images = extractor.extract(_request(store, ir, clip_bytes, "cup", clip_key=key))

        assert images == (), name


@needs_ffmpeg
def test_vlm_crop_does_not_guess_which_attribute_a_bare_value_means(store, ir, clip_bytes):
    """값만 온 이름('red')이나 다른 술어는 여전히 버린다: 어느 속성의 판정인지 알 수 없다."""
    judged = _judged("cup", {"red": "no", "size": "no"})
    llm = ReplayLLMClient([_answer(judged)])

    (image,) = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8).extract(
        _request(store, ir, clip_bytes, "cup")
    )

    assert image.detail["attributes"] == {}


@needs_ffmpeg
def test_vlm_crop_extracts_a_clip_once_when_two_jobs_ask_at_the_same_time(store, ir, clip_bytes):
    """같은 클립의 기준을 두 작업이 동시에 뽑으면, 뒤에 온 쪽은 앞선 쪽이 끝나기를 기다렸다가 그 기준을 쓴다.

    VLM 이 짚는 박스는 호출마다 조금씩 달라서, 둘이 따로 물으면 기준 이미지가 서로 달라지고
    (= 생성 캐시·작업 원장의 키가 달라지고) 호출도 두 번 과금된다 (감사 A-27).
    """
    asking = threading.Semaphore(0)
    answer_now = threading.Event()

    class SlowModel(ReplayLLMClient):
        def complete_json(self, *args, **kwargs):
            asking.release()  # 모델을 부르는 중이다
            assert answer_now.wait(10)
            return super().complete_json(*args, **kwargs)

    llm = SlowModel([_located("cup"), _located("cup")])
    extractor = VlmCropIdentityExtractor(store, FFMPEG, llm, min_side=8)
    request = _request(store, ir, clip_bytes, "cup")
    results: list[tuple] = []
    jobs = [
        threading.Thread(target=lambda: results.append(extractor.extract(request)))
        for _ in range(2)
    ]

    jobs[0].start()
    assert asking.acquire(timeout=10)  # 첫 작업이 모델의 답을 기다리고 있다
    jobs[1].start()
    second_asked = asking.acquire(timeout=0.5)  # 그동안 둘째 작업도 모델을 불렀는가
    answer_now.set()
    for job in jobs:
        job.join(10)

    assert not second_asked
    assert len(llm.calls) == 1
    assert len(results) == 2
    assert [i.image_uri for i in results[0]] == [i.image_uri for i in results[1]] != []


@needs_ffmpeg
def test_vlm_failure_is_reported_as_an_adapter_error(store, ir, clip_bytes):
    from frame.application.errors import AdapterError

    with pytest.raises(AdapterError):
        VlmCropIdentityExtractor(store, FFMPEG, ReplayLLMClient([])).extract(
            _request(store, ir, clip_bytes, "cup")
        )


def test_extractors_that_read_video_need_ffmpeg(store):
    with pytest.raises(ConfigurationError, match="ffmpeg"):
        FrameIdentityExtractor(store, "")


def _size(image: bytes) -> tuple[int, int]:
    """ffmpeg 로 이미지 크기를 읽는다 (Pillow 없이)."""
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", "pipe:0", "-f", "null", "-"],
        input=image,
        capture_output=True,
        check=False,
    )
    import re

    match = re.search(r", (\d{2,5})x(\d{2,5})", proc.stderr.decode("utf-8", "replace"))
    assert match, proc.stderr[-300:]
    return int(match.group(1)), int(match.group(2))
