"""검수 단위(장면 하나 · 객체 하나)를 감싸는 방어 (감사 A-13, A-24).

한 단위의 실패가 다른 단위의 판정을 버리지 않아야 하고, 모델이 답하지 않으면 남은 단위마다 시간 제한만큼
다시 기다리지 않아야 한다. VLM 검수기 셋이 같은 방어를 쓴다.
"""

from __future__ import annotations

import logging

import pytest

from frame.adapters.checker._guard import UNEXPECTED, UnitGuard
from frame.application.errors import LLMBadResponse, LLMTimeout, LLMUnavailable, StorageError


def _raising(error: Exception):
    def work():
        raise error

    return work


def _judge(guard: UnitGuard, unit: str, work) -> str:
    return guard.judge(unit, work, undetermined=lambda reason: f"미판정<{reason}>")


def test_a_unit_that_judges_returns_what_it_judged():
    assert _judge(UnitGuard("요구 반영 검수"), "장면 intro", lambda: "판정") == "판정"


def test_an_adapter_error_leaves_that_unit_undetermined_with_its_code_and_message(caplog):
    guard = UnitGuard("요구 반영 검수")

    with caplog.at_level(logging.WARNING):
        first = _judge(guard, "장면 intro", _raising(StorageError("산출물이 없습니다: clip")))
    second = _judge(guard, "장면 closeup", lambda: "판정")

    assert first == "미판정<STORAGE_ERROR: 산출물이 없습니다: clip>"
    assert second == "판정"  # 다른 단위는 그대로 판정한다
    assert "요구 반영 검수 미판정 (장면 intro): STORAGE_ERROR" in caplog.text


def test_an_unexpected_error_leaves_only_that_unit_undetermined(caplog):
    """예전에는 검수기 전체가 예외로 끝나 다른 단위의 판정까지 버려졌다 (검수 묶음이 미판정 한 건으로 바꿨다)."""
    guard = UnitGuard("일관성 검수")

    with caplog.at_level(logging.ERROR):
        first = _judge(guard, "객체 cup", _raising(KeyError("C:/secret/path")))
    second = _judge(guard, "객체 barista", lambda: "판정")

    assert (
        first == f"미판정<{UNEXPECTED}: KeyError>"
    )  # 사유에는 예외의 종류만 싣는다 (문구는 로그에)
    assert second == "판정"
    record = caplog.records[-1]
    assert record.exc_info is not None and "객체 cup" in record.getMessage()


@pytest.mark.parametrize(
    "error",
    [LLMTimeout("VLM 응답 없음"), LLMUnavailable("연결할 수 없음")],
    ids=["timeout", "unavailable"],
)
def test_once_the_model_does_not_answer_the_remaining_units_are_not_asked(error):
    guard = UnitGuard("요구 반영 검수")
    asked = []

    def ask_again():
        guard.about_to_ask()
        asked.append("closeup")
        return "판정"

    first = _judge(guard, "장면 intro", _raising(error))
    second = _judge(guard, "장면 closeup", ask_again)

    assert first == f"미판정<{error.code}: {error}>"
    assert asked == []
    assert second.startswith(f"미판정<{error.code}: ") and "묻지 않" in second


def test_a_unit_that_does_not_need_the_model_is_still_judged_after_the_model_stopped_answering():
    guard = UnitGuard("유지 요소 검수")
    _judge(guard, "장면 intro", _raising(LLMTimeout("VLM 응답 없음")))

    # 모델을 부르기 전에 끝나는 단위 (볼 요소가 없다, 클립이 영상이 아니다): about_to_ask 까지 가지 않는다
    assert _judge(guard, "장면 closeup", lambda: "볼 요소 없음") == "볼 요소 없음"


def test_an_answer_that_cannot_be_read_does_not_stop_the_remaining_units():
    guard = UnitGuard("요구 반영 검수")
    _judge(guard, "장면 intro", _raising(LLMBadResponse("응답을 해석하지 못했습니다")))

    guard.about_to_ask()  # 올라오는 것이 없다
    assert _judge(guard, "장면 closeup", lambda: "판정") == "판정"
