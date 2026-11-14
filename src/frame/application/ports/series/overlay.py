"""후반 합성 포트 (US-01·US-06, ADR-0027): 장면 클립에 화면 문구를 얹고, 화면의 글자를 읽는다.

화면 문구는 생성 모델이 아니라 여기서 그린다. 문구를 고치면 그 장면의 합성만 다시 하면 되고(무료·결정적),
다른 장면은 건드리지 않는다. 영상 안의 틀린 글자를 가리고 다시 쓰는 것(PATCH)도 같은 방법이다.

- OverlayRenderer : 장면 클립 하나에 문구를 얹는다.
- ScreenTextReader: 클립의 한 시각 화면에서 정해진 영역의 글자를 읽는다. 얹은 문구가 제대로 읽히는지 확인하는 데 쓴다.
구현: Fake(테스트), ffmpeg drawtext(추가 설치 없음) / 이미지를 읽는 LLM, OCR 엔진(설치 필요).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from frame.application.errors import AdapterError
from frame.domain.screen import Region
from frame.domain.series.episode import OverlayItem


class CompositionError(AdapterError):
    code = "COMPOSITION_ERROR"


class OverlayRenderer(ABC):
    """계약
    - 문구가 없으면 넘겨받은 클립을 그대로 돌려준다 (같은 바이트).
    - 문구가 있으면 그 문구의 시간(장면 안 시각)과 영역에만 그린다. 결과의 크기·fps·길이는 입력과 같다.
    - 결정적이다: 같은 클립과 같은 문구면 같은 결과다 (장면 단위 캐시의 전제).
    - signature 는 그리는 설정의 지문이다 (글꼴·인코딩 등). 같은 클립과 같은 문구에 다른 결과를 낼 수 있는
      설정이 바뀌면 달라진다. 합성 결과를 다시 쓸지 정하는 키에 들어간다: 글꼴을 바꾸면 다시 그린다.
    - 그리지 못하면 CompositionError (글꼴 없음 등은 기동 시점의 ConfigurationError).
    """

    @property
    @abstractmethod
    def signature(self) -> str: ...

    @abstractmethod
    def render(self, clip: bytes, overlays: Sequence[OverlayItem]) -> bytes: ...


class ScreenTextReader(ABC):
    """계약
    - 클립의 at_ms 시각 화면에서, 영역마다 읽은 글을 같은 순서로 돌려준다 (영역 수와 결과 수가 같다).
    - 그 영역에 글자가 없으면 빈 문자열이다 (예외가 아니다).
    - 읽지 못했으면 (모델이 그 영역에 답하지 않음 등) AdapterError 다. '글자 없음' 과 '읽지 못함' 을 같은 값으로
      돌려주지 않는다: 검수는 앞의 것을 어긋난 곳으로, 뒤의 것을 판정하지 못한 것으로 다룬다.
    - 읽은 그대로 적는다. 고치거나 채워 넣지 않는다.
    - 호출 실패는 AdapterError.
    """

    @abstractmethod
    def read(self, clip: bytes, at_ms: int, regions: Sequence[Region]) -> tuple[str, ...]: ...
