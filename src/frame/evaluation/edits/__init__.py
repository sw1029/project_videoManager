"""지표 1 평가 - 단일 속성 수정 케이스의 성공률과 비대상 변경률 (T12, ADR-0022).

맡는 개념
    수정 케이스 하나(단일 속성 수정)를 화면이 하는 순서대로 실행하고, 그 기록을 판정해 성공률 · 비대상 변경률을 낸다.
    모델을 직접 부르지 않는다 - 실행할 유스케이스 묶음(EditApp)은 부르는 쪽(tools/evaluate_edits · 테스트)이
    조립해 넘긴다.

밖에서 쓰는 이름
    케이스      EditCaseSpec · load_cases · parse_cases (케이스 파일 JSON), CaseFileError
    실행        run_case -> CaseRun, EditApp (실행에 필요한 유스케이스의 모양), wait_for_version · WaitTimeout,
                CaseInterrupted (Ctrl+C 까지의 기록), DEFAULT_CASE_TIMEOUT_S · DEFAULT_POLL_S
    판정        judge -> CaseJudgement, target_requirement_keys, left_unfinished
    지표        EditCase, evaluate_case -> EditCaseOutcome, evaluate_cases -> EditEvaluation, select_preservation,
                목표값 SUCCESS_RATE_TARGET · NON_TARGET_CHANGE_TARGET, 측정 기준 PRESERVATION_BASES
    결과 파일   build_report, TOOL_NAME · REPORT_VERSION · TARGETS, 가린 경로의 표시 LOCAL_PATH_MASK

모듈 지도 (안쪽 모듈은 _ 로 시작하고 이 패키지 밖에서 직접 import 하지 않는다)
    _cases.py      케이스 파일(JSON) 읽기
    _runner.py     케이스 실행: 변경 계획 -> 수정 버전 -> 생성 접수 -> 완료 대기 -> 검수 결과 (실행 기록 CaseRun)
    _report.py     실행 기록의 판정과 결과 파일(JSON)의 내용. 자유 문구의 이 컴퓨터 경로를 가린다
    _metrics.py    지표의 식과 목표값 (순수 함수)
    의존 방향: _cases · _metrics <- _runner <- _report.
    이 패키지는 domain · application 만 import 한다 (adapters · config 는 tools · 테스트가 조립한다).
"""

from ._cases import CaseFileError, EditCaseSpec, load_cases, parse_cases
from ._metrics import (
    NON_TARGET_CHANGE_TARGET,
    PRESERVATION_BASES,
    SUCCESS_RATE_TARGET,
    EditCase,
    EditCaseOutcome,
    EditEvaluation,
    evaluate_case,
    evaluate_cases,
    select_preservation,
)
from ._report import (
    LOCAL_PATH_MASK,
    REPORT_VERSION,
    TARGETS,
    TOOL_NAME,
    CaseJudgement,
    build_report,
    judge,
    left_unfinished,
    target_requirement_keys,
)
from ._runner import (
    DEFAULT_CASE_TIMEOUT_S,
    DEFAULT_POLL_S,
    CaseInterrupted,
    CaseRun,
    EditApp,
    WaitTimeout,
    run_case,
    wait_for_version,
)

__all__ = [
    "DEFAULT_CASE_TIMEOUT_S",
    "DEFAULT_POLL_S",
    "LOCAL_PATH_MASK",
    "NON_TARGET_CHANGE_TARGET",
    "PRESERVATION_BASES",
    "REPORT_VERSION",
    "SUCCESS_RATE_TARGET",
    "TARGETS",
    "TOOL_NAME",
    "CaseFileError",
    "CaseInterrupted",
    "CaseJudgement",
    "CaseRun",
    "EditApp",
    "EditCase",
    "EditCaseOutcome",
    "EditCaseSpec",
    "EditEvaluation",
    "WaitTimeout",
    "build_report",
    "evaluate_case",
    "evaluate_cases",
    "judge",
    "left_unfinished",
    "load_cases",
    "parse_cases",
    "run_case",
    "select_preservation",
    "target_requirement_keys",
    "wait_for_version",
]
