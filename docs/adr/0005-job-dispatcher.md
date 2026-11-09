# ADR-0005 비동기 작업은 디스패처 포트로 추상화

- 상태: 채택 (2026-11-07)

## 맥락
구조화와 생성은 수 초~수 분이 걸리므로 API 는 202 로 접수하고 워커가 처리해야 한다.
그러나 Sprint 1 에서 Celery/Redis 까지 도입하면 환경 구축 비용이 크고, 테스트에서 비동기 타이밍을 다루기 어렵다.

## 결정
`JobDispatcher.dispatch(Job)` 포트와 `JobHandlerRegistry` 만 정의한다.
- 테스트: `RecordingJobDispatcher` (쌓아 두고 원하는 시점에 실행)
- 데모·개발: `InlineJobDispatcher`, `ThreadPoolJobDispatcher`
- 운영(향후): Celery/RQ 어댑터 — 핸들러 레지스트리를 워커 프로세스에서 그대로 쓴다
유스케이스는 커밋 후에만 작업을 넘긴다 (워커가 저장되지 않은 버전을 읽지 않도록).

## 결과
- 상태 전이(QUEUED/PROCESSING) 검증이 결정적 테스트로 가능하다.
- 큐 교체가 설정 키 하나로 끝난다.
