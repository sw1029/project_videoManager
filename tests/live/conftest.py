"""실제 호출 테스트의 공통 스위치 (ADR-0030).

이 폴더의 테스트는 유료 API 를 부른다. FRAME_LIVE_TESTS=1 을 명시했을 때만 돌고, 그렇지 않으면 건너뛴다.
서버를 띄울 때 쓰는 설정(FRAME_GENERATOR=external_api, FRAME_STRUCTURER=llm)이 셸에 남아 있어도
이 스위치 없이는 부르지 않는다.

    FRAME_LIVE_TESTS=1 FRAME_STRUCTURER=llm FRAME_LLM_KEY_FILE=<키 파일> python -m pytest tests/live -m slow -s
"""

from __future__ import annotations

import os

import pytest

from tests.support.switches import LIVE_TESTS_VARIABLE, live_tests_enabled


@pytest.fixture(autouse=True)
def _live_tests_switch() -> None:
    if not live_tests_enabled(os.environ):
        pytest.skip(
            f"{LIVE_TESTS_VARIABLE}=1 을 명시하지 않으면 실제 호출 테스트를 건너뜀 (비용 발생)"
        )
