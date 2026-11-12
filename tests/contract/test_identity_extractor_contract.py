"""IdentityReferenceExtractor 계약 테스트 (ADR-0015). 구현은 params 로 추가한다.

- fake: 영상을 읽지 않는다.
- frame / vlm_crop: 실제 ffmpeg 로 합성 클립에서 프레임을 뽑는다 (ffmpeg 가 없으면 건너뜀). VLM 은 재생 클라이언트.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from frame.adapters.identity import (
    FakeIdentityExtractor,
    FrameIdentityExtractor,
    VlmCropIdentityExtractor,
)
from frame.adapters.llm import ReplayLLMClient
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
