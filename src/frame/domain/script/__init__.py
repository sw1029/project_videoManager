"""대본 — 소리에서 뽑아 줄 단위로 다듬은 말 (ADR-0028).

맡는 개념: 영상·녹음의 말소리를 '줄' 로 적은 것. 줄마다 시작·끝, 글, 걸치는 장면, 신뢰도, 종류(내레이션·대사)가 있고,
말이 아닌 소리(무음·음악·소음)는 따로 적는다. 말이 없으면 줄이 빈 대본이다.
음성 인식처럼 시리즈 기능 밖에서도 쓰는 개념이라 domain/series 밖에 둔다. 외부 세계를 모른다 (순수 함수).

밖에서 쓰는 이름: 아래 모듈 지도에 적은 타입과 함수 (이 입구의 __all__). 안쪽 모듈을 직접 import 하지 않는다.

낱말
- 받아쓰기 (Transcript, domain/speech.py)  인식기가 준 그대로: 구간과 낱말의 시각
- 대본 (SpokenScript)                       받아쓰기를 줄 단위로 다듬은 것. 사람이 줄의 글을 고칠 수 있다
- 녹음 대본 (ScriptLine, domain/series/episode)  편에서 사람이 읽을 줄. 계획이지 뽑은 것이 아니다
- 수량 (Quantity)                           값 + 단위. 말한 '오천 원' 과 적은 '5,000원' 을 같은 것으로 본다

모듈 지도
- lines           SpokenLine · SpokenScript · SoundSpan · LineKind · SoundKind · SceneSpan
- segmentation    build_script (받아쓰기 -> 대본: 줄 나누기 · 잰 무음에 맞추기 · 지어낸 문장 거르기) ·
                  classify_kinds · audible_spans · ScriptPolicy
- korean_numbers  한국어의 수 읽기: read_number · parse_number (오천 = 5,000 · 스물한 = 21)
- quantities      수량 (값 + 단위): quantities · quantity_conflicts · shared_quantities · mentions · canonical_text
"""

from frame.domain.script.korean_numbers import parse_number, read_number
from frame.domain.script.lines import (
    LineKind,
    SceneSpan,
    SoundKind,
    SoundSpan,
    SpokenLine,
    SpokenScript,
)
from frame.domain.script.quantities import (
    Quantity,
    QuantityConflict,
    canonical_text,
    mentions,
    quantities,
    quantity_conflicts,
    shared_quantities,
)
from frame.domain.script.segmentation import (
    DEFAULT_SCRIPT_POLICY,
    ScriptPolicy,
    audible_spans,
    build_script,
    classify_kinds,
)

__all__ = [
    "DEFAULT_SCRIPT_POLICY",
    "LineKind",
    "Quantity",
    "QuantityConflict",
    "SceneSpan",
    "ScriptPolicy",
    "SoundKind",
    "SoundSpan",
    "SpokenLine",
    "SpokenScript",
    "audible_spans",
    "build_script",
    "canonical_text",
    "classify_kinds",
    "mentions",
    "parse_number",
    "quantities",
    "quantity_conflicts",
    "read_number",
    "shared_quantities",
]
