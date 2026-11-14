"""구조화 골드셋 평가 (US-06 AC-11, T2·T6, docs/STRUCTURING_DESIGN.md §10).

맡는 개념
    자연어 브리프를 IR 로 바꾼 결과를 기대 IR(골드)과 견주어 수치로 채점하고, 실행 결과를 기록·비교한다.
    모델을 직접 부르지 않는다 — 구조화기는 부르는 쪽(테스트·tools)이 넘긴다.

밖에서 쓰는 이름
    골드      GoldCase · load_gold_cases (사례 파일), GoldSpec · GoldEntity · GoldRequirement (주석 붙은 기대 IR)
    채점      score_case · failed_case → CaseScore, Tally, ValueMismatch, values_match (값 비교 규칙)
    집계      summarize → StructuringEvaluation, headline, compare_headlines → MetricChange, 목표치 *_TARGET
    실행·기록  run_gold_set → CaseRun, StructuringRunDetails (실행 정보를 내주는 구조화기의 모양),
              build_record, write_record · save_record · read_record · latest_record,
              recorded_responses · rescore_record (모델 없이 다시 채점), compare_records → RecordComparison · CaseChange
    출력      format_summary · format_comparison

모듈 지도 (안쪽 모듈은 _ 로 시작하고 이 패키지 밖에서 직접 import 하지 않는다)
    _values.py    값 비교: 어휘 정규화 뒤 값 종류별 비교, 영어 어간·한국어 색 이름·붙여 쓰기
    _gold.py      골드: 주석 붙은 기대 IR 읽기, 골드 사례 파일과 골드셋 지문
    _matching.py  짝 맞추기: 모델 객체 ↔ 골드 객체(종류·별칭), 모델 장면 → 골드 장면(시간 겹침)
    _scoring.py   한 사례 채점: 객체·요구조건·등장 장면의 tp/fp/fn, 장면 수·경계 오차
    _summary.py   여러 사례 집계, 목표 판정, 대표 지표 비교
    _public.py    커밋되는 기록에 남길 값: 오류 문구의 경로·사용자 폴더 가리기, URL 의 계정·질의 빼기
    _records.py   골드셋 실행, 기록 JSON 만들기·읽기·다시 채점, 기록 비교
    _report.py    사람이 읽는 표
    의존 방향: _values ← _gold ← _matching ← _scoring ← _summary ← _records ← _report, _public ← _records.
    이 패키지는 domain·application 만 import 한다 (adapters·config 는 tools·테스트가 조립한다).
"""

from ._gold import GoldCase, GoldEntity, GoldRequirement, GoldSpec, load_gold_cases
from ._records import (
    CaseChange,
    CaseRun,
    RecordComparison,
    StructuringRunDetails,
    build_record,
    compare_records,
    latest_record,
    read_record,
    recorded_responses,
    rescore_record,
    run_gold_set,
    save_record,
    write_record,
)
from ._report import format_comparison, format_summary
from ._scoring import CaseScore, Tally, ValueMismatch, failed_case, score_case
from ._summary import (
    ENTITY_RECALL_TARGET,
    REQUIREMENT_F1_TARGET,
    SCENE_COUNT_MATCH_TARGET,
    MetricChange,
    StructuringEvaluation,
    compare_headlines,
    headline,
    summarize,
)
from ._values import values_match

__all__ = [
    # 골드
    "GoldCase",
    "GoldEntity",
    "GoldRequirement",
    "GoldSpec",
    "load_gold_cases",
    # 채점
    "CaseScore",
    "Tally",
    "ValueMismatch",
    "failed_case",
    "score_case",
    "values_match",
    # 집계
    "ENTITY_RECALL_TARGET",
    "REQUIREMENT_F1_TARGET",
    "SCENE_COUNT_MATCH_TARGET",
    "MetricChange",
    "StructuringEvaluation",
    "compare_headlines",
    "headline",
    "summarize",
    # 실행·기록
    "CaseChange",
    "CaseRun",
    "RecordComparison",
    "StructuringRunDetails",
    "build_record",
    "compare_records",
    "latest_record",
    "read_record",
    "recorded_responses",
    "rescore_record",
    "run_gold_set",
    "save_record",
    "write_record",
    # 출력
    "format_comparison",
    "format_summary",
]
