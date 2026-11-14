"""검수 단위(장면 하나 · 객체 하나)를 감싸는 방어. VLM 검수기 셋이 함께 쓴다.

검수기는 버전을 실패시키지 않는다 (ADR-0015 · 0018): 판정하지 못한 것은 미판정으로 남긴다. 그 약속을 단위마다 지킨다.
- 어댑터 오류 (프레임을 뽑지 못함, 모델이 답하지 않음, 답을 읽지 못함): 그 단위만 미판정, 사유는 "<코드>: <문구>".
- 그 밖의 예외 (예상하지 못한 것): 그 단위만 미판정, 사유는 예외의 종류. 문구는 로그에만 남긴다 (경로 같은 것이 섞인다).
  예전에는 검수기 전체가 예외로 끝나 다른 단위의 판정까지 버려졌다 (감사 A-13).
- 모델이 시간 안에 답하지 않았거나 닿지 않으면 (LLM_TIMEOUT · LLM_UNAVAILABLE) 그 실행의 남은 단위는 묻지 않는다.
  단위마다 시간 제한만큼 다시 기다리지 않게 한다 (감사 A-24). 답을 읽지 못한 것(LLM_BAD_RESPONSE)은 그 단위의 일이라
  다음 단위는 그대로 묻는다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from frame.application.errors import AdapterError, LLMTimeout, LLMUnavailable

log = logging.getLogger(__name__)

T = TypeVar("T")

UNEXPECTED = "UNEXPECTED_ERROR"  # 예상하지 못한 예외로 판정하지 못한 단위의 사유 머리
_NO_ANSWER = (LLMTimeout, LLMUnavailable)


class _NotAsked(Exception):
    """앞선 단위에서 모델이 답하지 않아 이 단위는 묻지 않는다 (about_to_ask 가 올리고 judge 가 잡는다)."""


class UnitGuard:
    """검수 실행 한 번(check 한 번)에 하나씩 만든다. 모델이 답하지 않았다는 것을 그 실행 동안 기억한다."""

    def __init__(self, checker: str) -> None:
        self._checker = checker  # 로그에 적을 검수기 이름 ("요구 반영 검수")
        self._no_answer: AdapterError | None = None

    def judge(self, unit: str, work: Callable[[], T], *, undetermined: Callable[[str], T]) -> T:
        """단위 하나를 판정한다. 판정하지 못하면 undetermined(사유) 가 만든 미판정 결과를 돌려준다."""
        try:
            return work()
        except _NotAsked as exc:
            return undetermined(str(exc))
        except AdapterError as exc:
            if isinstance(exc, _NO_ANSWER):
                self._no_answer = exc
            log.warning("%s 미판정 (%s): %s: %s", self._checker, unit, exc.code, exc)
            return undetermined(f"{exc.code}: {exc}")
        except Exception as exc:  # noqa: BLE001 - 한 단위의 예상 밖 오류가 다른 단위의 판정을 버리지 않게 한다
            log.exception("%s 미판정 (%s): 예상하지 못한 오류", self._checker, unit)
            return undetermined(f"{UNEXPECTED}: {type(exc).__name__}")

    def about_to_ask(self) -> None:
        """모델을 부르기 직전에 부른다. 이 실행에서 모델이 이미 답하지 않았으면 이 단위는 묻지 않는다."""
        if self._no_answer is not None:
            raise _NotAsked(
                f"{self._no_answer.code}: 앞선 판정에서 모델이 답하지 않아 이 단위는 묻지 않음"
            )
