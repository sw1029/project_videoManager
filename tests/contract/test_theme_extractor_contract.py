"""테마 추출 포트의 계약 테스트 (ADR-0025): ThemeExtractor.

Fake 와 프레임을 읽는 구현이 같은 규약을 지킨다. 실제 구현은 자리 수를 세어 답하는 가짜 모델로 돌린다 (과금 없음).
프레임은 진짜 ffmpeg 로 합성한 영상에서 뽑는다 (없으면 건너뜀).
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from frame.adapters.fake_media import fake_video
from frame.adapters.series.theme_extraction import FakeThemeExtractor
from frame.adapters.series.theme_extraction.vlm import VlmThemeExtractor
from frame.application.ports.llm import JsonCompletion, LLMClient
from frame.application.ports.series.theme_extraction import (
    ThemeExtractionError,
    ThemeExtractionRequest,
)
from frame.config.theme_settings import ThemeSettings
from frame.domain.series.source_analysis import VideoAnalysis, shots_from_cuts
from frame.domain.series.theme import SourceOrigin
from frame.domain.speech import SpeechSegment, Transcript
from tests.support.media import make_cut_clip
from tests.support.themes import cafe_source
from tests.support.video import find_ffmpeg

FFMPEG = find_ffmpeg()


# ---- ThemeExtractor ----
def _request(video: bytes, *, origin=SourceOrigin.FRAME_GENERATED, cuts=(4000, 8000, 12000)):
    source = cafe_source(origin)
    analysis = VideoAnalysis(
        media=source.media,
        shots=shots_from_cuts(cuts, source.media.duration_ms),
        transcript=Transcript(
            "ko",
            (
                SpeechSegment(400, 2600, "안녕하세요, 오늘의 메뉴예요."),
                SpeechSegment(4500, 6200, "바닐라 라떼입니다."),
                SpeechSegment(8600, 10400, "가격은 4,500원."),
                SpeechSegment(12500, 14000, "매장에서 만나요!"),
            ),
        ),
    )
    return ThemeExtractionRequest("thm_1", source, video, analysis, title="카페 신메뉴 소개")


class _SlotCountingLLM(LLMClient):
    """프레임을 읽는 모델 흉내: 질의에 적힌 자리 수만큼 답한다 (자리마다 진행자 하나)."""

    def complete_json(
        self, system, user, json_schema, *, schema_name="response", timeout_s=None, images=()
    ):
        count = len(re.findall(r"^S\d+ ", user, flags=re.MULTILINE))
        document = {
            "title": "모델이 지은 제목",
            "summary": "",
            "entities": [
                {
                    "entity_key": "host",
                    "kind": "PERSON",
                    "description": "진행자",
                    "recurring": True,
                    "role": "진행자",
                    "fixed_attributes": [{"predicate": "outfit", "value": "white shirt"}],
                }
            ],
            "slots": [
                {
                    "slot": f"S{i}",
                    "purpose": f"자리 {i}",
                    "description": f"장면 {i}",
                    "entity_keys": ["host"],
                    "requirements": [],
                    "narration_purpose": "",
                }
                for i in range(1, count + 1)
            ],
            "style": {"conditions": [], "palette": [], "notes": ""},
            "screen_texts": [],
            "text_rules": [],
            "variables": [],
            "narration_tone": "",
            "music": {"present": None, "mood": ""},
        }
        return JsonCompletion(json.dumps(document, ensure_ascii=False), "slot-counting")


@pytest.fixture(scope="module")
def real_video(tmp_path_factory) -> bytes | None:
    if FFMPEG is None:
        return None
    folder = tmp_path_factory.mktemp("theme")
    colors = ("red", "blue", "green", "yellow")
    return make_cut_clip(FFMPEG, folder / "cafe.mp4", colors=colors, seconds_each=4.0)


@pytest.fixture(params=["fake", "vlm"])
def extractor_case(request, real_video):
    """(추출기를 만드는 함수, 영상 바이트). 추출기는 요청마다 새로 만든다."""
    if request.param == "vlm":
        if FFMPEG is None:
            pytest.skip("ffmpeg 없음 (video extra 미설치)")
        defaults = ThemeSettings()
        return (
            lambda: VlmThemeExtractor(
                FFMPEG,
                _SlotCountingLLM(),
                frames_per_slot=defaults.extractor_frames,
                max_attempts=defaults.extractor_attempts,
            ),
            real_video,
        )
    return (lambda: FakeThemeExtractor(), fake_video())


def _assert_extraction_contract(extraction, request) -> None:
    theme, ir = extraction.theme, extraction.source_ir
    theme.validate()
    ir.validate()
    assert theme.theme_id == request.theme_id and theme.source == request.source
    # 원본 IR 의 장면 = 테마의 자리 (키·순서·길이)
    assert [s.scene_key for s in ir.scenes] == [s.slot_key for s in theme.format.slots]
    assert [s.duration_ms for s in ir.scenes] == [s.duration_ms for s in theme.format.slots]
    total = request.analysis.media.duration_ms
    assert abs(theme.format.total_duration_ms - total) < 100
    assert theme.format.total_duration_ms % 100 == 0
    if total >= request.min_slot_ms:
        assert all(slot.duration_ms >= request.min_slot_ms for slot in theme.format.slots)
    assert {c.entity_key for c in theme.cast} <= {e.entity_key for e in ir.entities}
    for text in extraction.screen_texts:
        assert 0 <= text.start_ms < text.end_ms <= total + 100


def test_extraction_gives_a_valid_theme_and_source_ir(extractor_case):
    make, video = extractor_case
    request = _request(video)
    extraction = make().extract(request)

    _assert_extraction_contract(extraction, request)
    assert extraction.theme.title == "카페 신메뉴 소개"
    assert len(extraction.theme.format.slots) == 4
    assert extraction.theme.format.observed_shot_ms == (4000, 4000, 4000, 4000)


def test_short_shots_are_merged_into_reproducible_slots(extractor_case):
    make, video = extractor_case
    request = _request(video, cuts=(1000, 2000, 3000, 9000, 15000))
    extraction = make().extract(request)

    _assert_extraction_contract(extraction, request)
    assert [s.duration_ms for s in extraction.theme.format.slots] == [3000, 6000, 7000]
    assert extraction.theme.format.observed_shot_ms == (1000, 1000, 1000, 6000, 6000, 1000)


def test_narration_of_the_source_is_kept_in_the_theme(extractor_case):
    make, video = extractor_case
    request = _request(video)
    narration = make().extract(request).theme.narration

    assert narration.present and narration.language == "ko"
    assert narration.pace_cps == pytest.approx(request.analysis.transcript.pace_cps)
    assert {b.slot_key for b in narration.beats} == {
        s.slot_key for s in make().extract(request).theme.format.slots
    }


def test_silent_source_has_no_narration(extractor_case):
    make, video = extractor_case
    request = _request(video)
    silent = replace(request, analysis=replace(request.analysis, transcript=None))
    narration = make().extract(silent).theme.narration

    assert not narration.present and narration.pace_cps is None and narration.beats == ()


def test_external_source_gets_no_reference_images(extractor_case):
    make, video = extractor_case
    request = _request(video, origin=SourceOrigin.EXTERNAL)
    extraction = make().extract(request)

    _assert_extraction_contract(extraction, request)
    assert extraction.theme.references() == ()


def test_extractor_failure_is_an_adapter_error():
    failing = FakeThemeExtractor(error=ThemeExtractionError("주입된 오류"))
    with pytest.raises(ThemeExtractionError):
        failing.extract(_request(fake_video()))
