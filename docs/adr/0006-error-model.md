# ADR-0006 실행 오류와 검수 판정의 분리

- 상태: 채택 (2026-11-07)

## 맥락
문서는 "FAILED = 실행 오류, FAIL = 검수 기준 미달" 을 구분하고, 검수가 FAIL/UNDETERMINED 여도 처리 완료는 COMPLETED 라고 정한다.
또 미판정을 통과로 집계하면 지표가 부풀려진다.

## 결정
- 실행 오류: 파이프라인이 `PipelineFailure(stage, error_code, message)` 로 정규화 → `VersionState.FAILED` + `error_code` + `"[stage] message"`.
  `error_code` 는 어댑터 예외의 `code` (`GENERATION_ERROR`, `TRACKING_ERROR` …) 또는 `UNEXPECTED_ERROR` 를 그대로 쓴다.
- 검수 판정: `CheckResult.verdict ∈ {PASS, FAIL, UNDETERMINED}`. 판정 불가(추적 실패 등)는 `UNDETERMINED`.
- 지표: `UNDETERMINED` 는 분모에 포함, 분자에서 제외.
- API: 도메인 오류의 `code` 가 그대로 `error_code` 로 노출된다 (404/409/422 매핑).

## 결과
- 대시보드는 "왜 실패했는가(실행)" 와 "무엇이 미달인가(검수)" 를 구분해 보여줄 수 있다 (US-04).
