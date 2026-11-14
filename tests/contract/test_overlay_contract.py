"""후반 합성 포트의 계약 테스트 (ADR-0027): OverlayRenderer·ScreenTextReader.

Fake 와 실제 구현이 같은 규약을 지킨다. ffmpeg 구현은 진짜 ffmpeg 와 한글 글꼴로 합성한다 (없으면 건너뜀).
VLM 읽기는 재생 응답으로 돌린다 (네트워크·과금 없음).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

import pytest

from frame.adapters.llm import ReplayLLMClient
from frame.adapters.media_tools import probe_media, region_ssim
from frame.adapters.series.overlay import FakeOverlayRenderer, FakeScreenTextReader
from frame.adapters.series.overlay.ffmpeg_renderer import (
    FfmpegOverlayRenderer,
    estimate_text_width,
    fit_font_px,
    resolve_font,
)
from frame.adapters.series.overlay.vlm_reader import VlmScreenTextReader
from frame.application.errors import AdapterError, ConfigurationError, LLMTimeout
from frame.application.ports.series.overlay import CompositionError, OverlayRenderer
from frame.domain.screen import Region, TextRole
from frame.domain.series.episode import OverlayItem, OverlayKind
from frame.domain.series.theme import TextStyle
from tests.support.video import find_ffmpeg, make_clip

FFMPEG = find_ffmpeg()


def _font() -> str | None:
    try:
        return resolve_font()
    except ConfigurationError:
        return None


FONT = _font()
needs_ffmpeg = pytest.mark.skipif(
    FFMPEG is None or FONT is None, reason="ffmpeg 또는 한글 글꼴 없음"
)

PRICE_REGION = Region(0.1, 0.7, 0.8, 0.1)
PRICE = OverlayItem(
    "closeup:price",
    "closeup",
    "말차 라떼 5,000원",
    1000,
    3000,
    PRICE_REGION,
    TextStyle(color="#ffe08a", size=0.06),
    TextRole.PRICE,
    "price",
)
TITLE = OverlayItem(
    "closeup:title",
    "closeup",
    "오늘의 메뉴",
    0,
    4000,
    Region(0.1, 0.05, 0.8, 0.1),
    role=TextRole.TITLE,
)


@dataclass
class Kit:
    name: str
    renderer: OverlayRenderer
    clip: bytes  # 4초 클립


@pytest.fixture(scope="module")
def ffmpeg_kit(tmp_path_factory) -> Kit:
    if FFMPEG is None or FONT is None:
        pytest.skip("ffmpeg 또는 한글 글꼴 없음")
    folder = tmp_path_factory.mktemp("overlay")
    clip = make_clip(FFMPEG, folder / "clip.mp4", size="240x426", fps=10)
    return Kit("ffmpeg", FfmpegOverlayRenderer(FFMPEG, FONT), clip)


@pytest.fixture(scope="module", params=["fake", "ffmpeg"])
def kit(request) -> Kit:
    if request.param == "fake":
        return Kit("fake", FakeOverlayRenderer(), b'{"scene_key": "closeup", "fake": "clip"}')
    return request.getfixturevalue("ffmpeg_kit")


# ---- OverlayRenderer ----
def test_clip_without_overlays_is_returned_untouched(kit):
    assert kit.renderer.render(kit.clip, ()) is kit.clip


def test_rendering_changes_the_clip_and_is_deterministic(kit):
    first = kit.renderer.render(kit.clip, (PRICE,))
    second = kit.renderer.render(kit.clip, (PRICE,))

    assert first != kit.clip
    assert first == second  # 같은 클립·같은 문구 -> 같은 결과 (장면 단위 캐시의 전제)
    assert kit.renderer.render(kit.clip, (replace(PRICE, text="말차 라떼 5,500원"),)) != first


def test_fake_composite_records_what_was_drawn():
    renderer = FakeOverlayRenderer()
    clip = b"clip-bytes"
    once = renderer.render(clip, (PRICE,))
    twice = renderer.render(once, (TITLE,))  # 고친 문구를 덧얹어도 바탕 클립은 같다

    composite = json.loads(twice)["fake_composite"]
    assert [o["overlay_id"] for o in composite["overlays"]] == ["closeup:price", "closeup:title"]
    assert composite["clip_sha256"] == json.loads(once)["fake_composite"]["clip_sha256"]
    assert renderer.renders == [(PRICE,), (TITLE,)]
    with pytest.raises(CompositionError):
        FakeOverlayRenderer(error=CompositionError("주입된 오류")).render(clip, (PRICE,))


def test_ffmpeg_output_keeps_size_rate_and_length(ffmpeg_kit, tmp_path):
    kit = ffmpeg_kit
    source, target = tmp_path / "source.mp4", tmp_path / "composed.mp4"
    source.write_bytes(kit.clip)
    target.write_bytes(kit.renderer.render(kit.clip, (PRICE, TITLE)))
    before, after = probe_media(FFMPEG, source), probe_media(FFMPEG, target)

    assert (after.width, after.height, after.fps, after.frames) == (
        before.width,
        before.height,
        before.fps,
        before.frames,
    )
    assert after.video_codec == "h264" and not after.has_audio


def test_ffmpeg_draws_only_inside_the_region_and_the_time_span(ffmpeg_kit):
    """문구의 시간·영역 밖은 그대로다 (다시 인코딩한 만큼만 다르다)."""
    kit = ffmpeg_kit
    composed = kit.renderer.render(kit.clip, (PRICE,))
    box = (PRICE_REGION.x, PRICE_REGION.y, PRICE_REGION.w, PRICE_REGION.h)
    shown, hidden = (1000, 3000), (0, 1000)

    inside_shown = region_ssim(FFMPEG, kit.clip, composed, box, inside=True, span_ms=shown)
    inside_hidden = region_ssim(FFMPEG, kit.clip, composed, box, inside=True, span_ms=hidden)
    outside_shown = region_ssim(FFMPEG, kit.clip, composed, box, inside=False, span_ms=shown)

    assert inside_shown < 0.8  # 문구가 그려진 곳은 달라졌다
    assert inside_hidden > 0.95  # 같은 자리라도 문구의 시간이 아니면 그대로
    assert outside_shown > 0.95  # 문구의 시간이라도 영역 밖은 그대로


def test_ffmpeg_rejects_values_that_would_break_the_filter(ffmpeg_kit):
    kit = ffmpeg_kit
    bad_color = replace(PRICE, style=TextStyle(color="white:enable=0"))
    with pytest.raises(CompositionError, match="색"):
        kit.renderer.render(kit.clip, (bad_color,))
    with pytest.raises(CompositionError):
        kit.renderer.render(b"not a video", (PRICE,))
    # 글자는 파일로 넘기므로 필터 문법의 글자가 들어 있어도 그대로 그려진다
    tricky = replace(PRICE, text="50% 할인: 'A,B' [오늘만]; \\ %{pts}")
    assert kit.renderer.render(kit.clip, (tricky,)) != kit.clip


def test_text_is_shrunk_to_fit_its_region():
    assert estimate_text_width("말차 라떼", 40) == pytest.approx((4 * 1.0 + 0.3) * 40)
    assert estimate_text_width("FRAME 2026", 40) == pytest.approx((9 * 0.55 + 0.3) * 40)
    assert fit_font_px("오늘의 메뉴", 40, 400, 80) == 40  # 들어가면 그대로
    shrunk = fit_font_px("아주 아주 아주 아주 긴 화면 문구입니다", 40, 200, 80)
    assert 10 <= shrunk < 40
    assert estimate_text_width("아주 아주 아주 아주 긴 화면 문구입니다", shrunk) <= 200
    assert fit_font_px("오늘의 메뉴", 40, 400, 20) == 18  # 영역의 높이를 넘지 않는다


def test_font_must_exist(tmp_path):
    with pytest.raises(ConfigurationError, match="FRAME_OVERLAY_FONT"):
        resolve_font(str(tmp_path / "missing.ttf"))
    with pytest.raises(ConfigurationError):
        resolve_font(candidates=(str(tmp_path / "none.ttf"),))
    font = tmp_path / "font.ttf"
    font.write_bytes(b"not really a font")
    assert resolve_font(str(font)) == str(font)
    assert resolve_font(candidates=(str(tmp_path / "none.ttf"), str(font))) == str(font)


# ---- ScreenTextReader ----
def _composite(renderer=None) -> bytes:
    return (renderer or FakeOverlayRenderer()).render(b"clip-bytes", (PRICE, TITLE))


def test_fake_reader_returns_what_is_shown_in_each_region():
    reader = FakeScreenTextReader()
    clip = _composite()

    assert reader.read(clip, 2000, (PRICE_REGION, TITLE.region)) == (
        "말차 라떼 5,000원",
        "오늘의 메뉴",
    )
    assert reader.read(clip, 500, (PRICE_REGION, TITLE.region)) == (
        "",
        "오늘의 메뉴",
    )  # 아직 안 나온 문구
    assert reader.read(clip, 2000, (Region(0.1, 0.4, 0.8, 0.1),)) == ("",)  # 문구가 없는 영역
    assert reader.read(b"clip-bytes", 2000, (PRICE_REGION,)) == ("",)  # 합성하지 않은 클립
    assert reader.read(clip, 2000, ()) == ()


def test_fake_reader_can_show_broken_text():
    reader = FakeScreenTextReader(misread={"closeup:price": "□□ □□ 5,000□"})

    assert reader.read(_composite(), 2000, (PRICE_REGION, TITLE.region)) == (
        "□□ □□ 5,000□",
        "오늘의 메뉴",
    )
    with pytest.raises(AdapterError):
        FakeScreenTextReader(error=LLMTimeout("주입된 오류")).read(
            _composite(), 2000, (PRICE_REGION,)
        )


def test_patch_drawn_later_covers_the_text_under_it():
    patch = replace(
        PRICE, overlay_id="closeup:patch", text="말차 라떼 5,500원", kind=OverlayKind.PATCH
    )
    clip = FakeOverlayRenderer().render(_composite(), (patch,))

    assert FakeScreenTextReader().read(clip, 2000, (PRICE_REGION,)) == ("말차 라떼 5,500원",)


@needs_ffmpeg
def test_vlm_reader_sends_one_crop_per_region_and_returns_texts_in_order(tmp_path):
    clip = make_clip(FFMPEG, tmp_path / "clip.mp4", size="240x426", fps=10)
    composed = FfmpegOverlayRenderer(FFMPEG, FONT).render(clip, (PRICE, TITLE))
    answer = {
        "texts": [{"id": "T2", "text": " 오늘의  메뉴 "}, {"id": "T1", "text": "말차 라떼 5,000원"}]
    }
    llm = ReplayLLMClient([json.dumps(answer, ensure_ascii=False)])
    reader = VlmScreenTextReader(FFMPEG, llm)

    texts = reader.read(composed, 2000, (PRICE_REGION, TITLE.region))

    assert texts == ("말차 라떼 5,000원", "오늘의 메뉴")  # 요청한 영역 순서, 공백 정리
    call = llm.calls[0]
    assert [image.label for image in call["images"]] == ["T1", "T2"]
    assert all(image.media_type == "image/png" for image in call["images"])
    assert "exactly" in call["system"] and call["schema_name"] == "screen_text"
    assert reader.last_completion is not None


@needs_ffmpeg
def test_vlm_reader_tells_no_text_from_not_read(tmp_path):
    """'글자가 없다' 는 답은 빈 문자열, 답이 빠진 영역은 읽지 못한 것(오류)이다."""
    clip = make_clip(FFMPEG, tmp_path / "clip.mp4", size="240x426", fps=10)
    empty = json.dumps({"texts": [{"id": "T1", "text": "FRAME"}, {"id": "T2", "text": ""}]})
    partial = json.dumps({"texts": [{"id": "T1", "text": "FRAME"}]})
    answers = [empty, partial, "not json", LLMTimeout("느림")]
    reader = VlmScreenTextReader(FFMPEG, ReplayLLMClient(answers))

    assert reader.read(clip, 1000, (PRICE_REGION, TITLE.region)) == ("FRAME", "")
    with pytest.raises(AdapterError, match="빠진 영역"):
        reader.read(clip, 1000, (PRICE_REGION, TITLE.region))
    with pytest.raises(AdapterError):
        reader.read(clip, 1000, (PRICE_REGION,))
    with pytest.raises(LLMTimeout):
        reader.read(clip, 1000, (PRICE_REGION,))
    assert reader.read(clip, 1000, ()) == ()  # 영역이 없으면 모델을 부르지 않는다


def test_signature_names_the_way_overlays_are_drawn(kit):
    """같은 설정이면 같은 지문이다. 장면 합성 결과를 다시 쓸지 정하는 키에 들어간다."""
    assert isinstance(kit.renderer.signature, str) and kit.renderer.signature
    assert kit.renderer.signature == kit.renderer.signature


@needs_ffmpeg
def test_ffmpeg_signature_changes_with_the_font_and_the_encoding(tmp_path):
    other = tmp_path / "other.ttf"
    other.write_bytes(b"not really a font")  # 있는 파일이면 된다 (그리지는 않는다)
    base = FfmpegOverlayRenderer(FFMPEG, FONT)

    assert FfmpegOverlayRenderer(FFMPEG, FONT).signature == base.signature
    assert FfmpegOverlayRenderer(FFMPEG, str(other)).signature != base.signature
    assert (
        FfmpegOverlayRenderer(FFMPEG, FONT, regular_font_file=str(other)).signature
        != base.signature
    )
    assert FfmpegOverlayRenderer(FFMPEG, FONT, crf=23).signature != base.signature
    # 지문에 폴더 이름은 넣지 않는다 (저장소의 키에 사용자의 경로가 남지 않게)
    assert str(tmp_path) not in FfmpegOverlayRenderer(FFMPEG, str(other)).signature
