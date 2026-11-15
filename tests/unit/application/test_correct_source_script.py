"""CorrectSourceScript: 원본에서 뽑은 대본의 줄을 고치고, 그 줄에 걸린 것만 다시 맞춘다 (ADR-0029)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from frame.adapters.shared.media.fake_media import fake_video
from frame.application.use_cases.series import (
    CorrectSourceScriptCommand,
    ExtractThemeCommand,
    LineCorrection,
)
from frame.domain.errors import NotFound, ValidationError
from frame.domain.screen import TextRole
from frame.domain.script import LineKind, SceneSpan, build_script
from frame.domain.series.script_evidence import DiscrepancyKind
from frame.domain.series.source_analysis import ScreenText
from frame.domain.speech import SpeechSegment, Transcript
from tests.support.series import save_theme, series_container, series_of

# 셋째 줄은 인식기가 "사천오백 원" 을 "오천 원" 으로 잘못 받아 적은 것이다
SAID = Transcript(
    "ko",
    (
        SpeechSegment(400, 2600, "안녕하세요, 오늘의 메뉴예요."),
        SpeechSegment(4500, 6200, "바닐라 라떼입니다."),
        SpeechSegment(8600, 10400, "가격은 오천 원."),
        SpeechSegment(12500, 14000, "매장에서 만나요!"),
    ),
)


def _correct(series, theme_id: str, *corrections: LineCorrection):
    return series.correct_source_script.execute(CorrectSourceScriptCommand(theme_id, corrections))


def _extracted(series):
    command = ExtractThemeCommand(fake_video(transcript=SAID), title="카페")
    return series.extract_theme.execute(command).record


def test_correcting_a_line_refits_only_what_hangs_on_it_without_any_paid_call():
    series = series_of(series_container())
    record = _extracted(series)
    theme_id = record.theme.theme_id
    calls = (len(series.recognizer.calls), len(series.extractor.requests))

    result = _correct(series, theme_id, LineCorrection("S3", "가격은 사천오백 원."))

    assert result.changed_lines == ("S3",)
    assert set(result.refit) == {"narration:slot_3", "pace"}
    saved = series.themes.get(theme_id)
    assert saved == result.record
    line = saved.script.line("S3")
    assert (line.text, line.heard, line.edited) == ("가격은 사천오백 원.", "가격은 오천 원.", True)
    assert saved.theme.narration.beat("slot_3").example == "가격은 사천오백 원."
    for slot in ("slot_1", "slot_2", "slot_4"):  # 다른 자리는 그대로다
        assert saved.theme.narration.beat(slot) == record.theme.narration.beat(slot)
    assert saved.source_ir == record.source_ir and saved.analysis == record.analysis
    # 음성 인식도 테마 추출도 다시 부르지 않았다
    assert (len(series.recognizer.calls), len(series.extractor.requests)) == calls


def test_a_discrepancy_between_screen_and_speech_is_resolved_by_fixing_the_line():
    container = series_container()
    series = series_of(container)
    base = save_theme(container)  # 카페 테마: 화면의 가격은 4,500원
    scenes = []
    cursor = 0
    for slot in base.theme.format.slots:
        scenes.append(SceneSpan(slot.slot_key, cursor, cursor + slot.duration_ms))
        cursor += slot.duration_ms
    record = replace(
        base,
        script=build_script(SAID, scenes, duration_ms=16000),
        screen_texts=(ScreenText("바닐라 라떼 4,500원", 8500, 11500, role=TextRole.PRICE),),
    )
    series.themes.save(record)

    result = _correct(series, "thm_cafe", LineCorrection("S3", "가격은 사천오백 원."))

    assert [d.kind for d in result.resolved] == [DiscrepancyKind.VALUE] and result.raised == ()
    assert result.record.evidence.discrepancies == ()
    assert result.record.theme.variables == base.theme.variables  # 화면에서 본 값은 그대로다


def test_line_kind_can_be_corrected_too():
    series = series_of(series_container())
    record = _extracted(series)

    result = _correct(series, record.theme.theme_id, LineCorrection("S2", kind=LineKind.DIALOGUE))

    assert result.record.script.line("S2").kind is LineKind.DIALOGUE
    assert result.record.theme.narration.beat("slot_2") is None  # 대사는 내레이션 규칙이 아니다


def test_what_cannot_be_corrected():
    container = series_container()
    series = series_of(container)
    extracted = _extracted(series)
    theme_id = extracted.theme.theme_id
    save_theme(container)  # 대본 없이 넣은 테마

    with pytest.raises(NotFound):
        _correct(series, "thm_none", LineCorrection("S1", "가"))
    with pytest.raises(ValidationError, match="고칠 대본이 없습니다"):
        _correct(series, "thm_cafe", LineCorrection("S1", "가"))
    with pytest.raises(NotFound):
        _correct(series, theme_id, LineCorrection("S9", "없는 줄"))
    with pytest.raises(ValidationError):
        _correct(series, theme_id, LineCorrection("S1", "하나"), LineCorrection("S1", "둘"))
    with pytest.raises(ValidationError):
        _correct(series, theme_id, LineCorrection("S1", "  "))
    # 같은 글로 고치면 아무것도 바뀌지 않는다
    same = _correct(series, theme_id, LineCorrection("S1", extracted.script.line("S1").text))
    assert same.changed_lines == () and same.record == extracted
    assert series.themes.get(theme_id) == extracted
