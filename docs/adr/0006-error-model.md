# ADR-0006 실행 오류와 검수 판정의 분리

- 상태: 채택 (2026-11-07)

## 맥락
문서는 "FAILED = 실행 오류, FAIL = 검수 기준 미달" 을 구분하고, 검수가 FAIL/UNDETERMINED 여도 처리 완료는 COMPLETED 라고 정한다.
또 미판정을 통과로 집계하면 지표가 부풀려진다.

## 결정
- 실행 오류: 파이프라인이 `PipelineFailure(stage, error_code, message)` 로 정규화 → `VersionState.FAILED` + `error_code` + `"[stage] message"`.
  `error_code` 는 어댑터 예외의 `code` (`GENERATION_ERROR`, `TRACKING_ERROR` …) 또는 `UNEXPECTED_ERROR` 를 그대로 쓴다.
- 검수 판정: `CheckResult.verdict ∈ {PASS, FAIL, UNDETERMINED}`. 판정 불가(추적 실패 등)는 `UNDETERMINED`.
  - 보완 (2026-11-14, 감사 A-13): 검수기가 예외로 끝난 것도 판정 불가다. `CheckSuite` 가 그 검수기의 자리에 `UNDETERMINED` 한 건
    (`detail.reason`·`checker`·`error`, 오류 원문은 로그에만)을 남기고 나머지 검수기를 계속한다. 버전은 실패가 되지 않는다 —
    유료 생성이 끝난 뒤이고 `FAILED` 는 되돌릴 수 없는 상태다. 그때까지는 검수기 하나의 예상 밖 예외가 버전을
    `FAILED(UNEXPECTED_ERROR)` 로 만들고 앞선 검수기의 결과까지 버렸다. 검수 단계가 실패하는 것은 검수 결과가 무결성 규칙을
    어길 때(이 버전에 없는 요구조건을 가리킴)뿐이다.
- 지표: `UNDETERMINED` 는 분모에 포함, 분자에서 제외.
- API: 도메인 오류의 `code` 가 그대로 `error_code` 로 노출된다 (404/409/422 매핑).

## 결과
- 대시보드는 "왜 실패했는가(실행)" 와 "무엇이 미달인가(검수)" 를 구분해 보여줄 수 있다 (US-04).
