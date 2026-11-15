"""모듈 크기 (ADR-0030): 새 모듈은 500줄을 넘지 않는다. 넘으면 개념 단위로 나눈다 (ADR-0031).

줄 수는 나누라는 신호일 뿐 기준이 아니다 — 나누는 기준은 개념이다. 지금 넘는 모듈은 baselines.LARGE_MODULES 에 있다.
"""

from __future__ import annotations

from tests.architecture.baselines import LARGE_MODULES
from tests.architecture.imports import line_count, modules

LIMIT = 500


def test_modules_stay_small_enough_to_read():
    large = {name for name in modules() if line_count(name) > LIMIT}

    assert large - LARGE_MODULES == set(), f"{LIMIT}줄을 넘는 새 모듈"
    assert LARGE_MODULES - large == set(), "이제 작아졌으니 기준선에서 지운다"
