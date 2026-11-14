# ADR-0012 생성 시간 제한, 실패 시 부분 결과 보존, 단계 진행 관찰

- 상태: 채택 (2026-11-12)
- 관련: 이슈 #1(생성 시간초과 처리), #3(실패 시 부분 결과 보존과 단계별 진행 관찰), ADR-0006(오류 모델), ADR-0011(재과금 방지)

## 맥락
- 실제 OpenRouter 장면 하나는 82~145초 걸렸다 (2026-11-11 실호출). 시간 제한은 OpenRouter 어댑터 안의
  `GenerationTimeout`(접수부터 완료까지의 폴링 구간, `FRAME_VIDEO_TIMEOUT_S`) 하나뿐이었다. 다운로드·ffmpeg 정규화가 멈추거나
  자체 제한이 없는 어댑터(diffusers 로컬 추론 등)가 멈추면 작업 스레드가 무기한 묶인다.
- 가짜 위쪽 서버 실험(2026-11-11): 장면 2 가 실패하면 버전은 FAILED 가 되지만, 과금된 장면 1 클립은 저장소에만 남고 버전에 연결되지
  않았다 (API 의 `clip_uri` 가 모두 `None`). 처리 중에는 몇 분 동안 `PROCESSING` 만 보이고 단계·장면 진행을 알 방법이 없었다.
- 파이썬 스레드는 밖에서 강제로 멈출 수 없다.

## 결정

### 1. 범용 시간 제한 `TimeoutVideoGenerator` (#1)
- `FRAME_GENERATOR_TIMEOUT_S`(초). 기본 0 = 끔. `0`·빈 값·`off`·`none` 이면 끔. 켜면 장면 하나(재시도 포함)에 쓰는 총 시간을 제한한다.
- 내부 호출을 daemon 작업 스레드에서 돌리고, 호출한 스레드는 마감에 돌아와 `GenerationTimeout(retryable=False)` 을 던진다.
  파이프라인 규칙 그대로 버전은 `FAILED(GENERATION_TIMEOUT)` 이 되고, 추적·검수는 실행되지 않으며, 부모 버전은 바뀌지 않는다.
- `retryable=False` 인 이유: 외부 작업이 이미 접수돼 계속 돌고 있을 수 있어 다시 제출하면 또 과금된다 (ADR-0011 규칙의 연장).
- 버려진 작업 스레드
  - 강제로 멈추지 않는다. 내부 호출이 스스로 끝날 때까지 돈다. OpenRouter 는 자체 제한까지 폴링한 뒤 끝나고,
    다운로드 중이었다면 끝까지 받아 저장소에 넣는다.
  - 결과는 버린다 (버전은 이미 FAILED). 과금됐을 수 있는 산출물의 URI 를 경고 로그(`frame.adapters.generator.decorators`)로
    남겨 손으로 회수할 수 있게 한다. 실패로 끝나면 그 오류를 경고로 남긴다.
    보완 (2026-11-15, 감사 K-05): 모듈 이름을 `resilience` 로 바꿔 로그 이름이 `frame.adapters.generator.resilience` 가 됐다.
  - 안쪽 Retry 가 다음 시도를 하지 않는다. 컨테이너는 `RetryingVideoGenerator` 의 대기 함수로 `pause_before_retry` 를 꽂는다.
    버려진 작업 스레드에서는 이 함수가 `GenerationTimeout(retryable=False)` 을 던져 재제출을 막는다 (재시도 대기 중이었다면 바로 깨어난다).
    `RetryingVideoGenerator` 코드는 바꾸지 않았다.
  - 보완 (2026-11-14, 감사 A-34): 외부 API 어댑터도 같은 신호를 본다. OpenRouter 어댑터는 매 제출(POST) 직전에 확인해,
    제출 전에 기다리던 스레드(같은 장면의 잠금, 부모 클립의 저장본 주소, 429·502 뒤의 대기)가 마감 뒤에 깨어나 접수시키지 않는다.
    그때까지는 재시도 데코레이터의 시도 사이만 막아서, 어댑터 안에서 기다리던 제출은 버전이 실패한 뒤에 접수됐다.
    신호는 `adapters/generator/abandonment.py` 에 있다 (`stop_if_abandoned`·`pause_unless_abandoned`).
    이미 접수된 작업의 폴링·내려받기는 지금처럼 끝까지 한다 (결과가 원장에 남아 다음 요청이 다시 내지 않는다).
  - daemon 스레드라 프로세스 종료를 막지 않는다. 종료 때 끝나지 않은 외부 작업은 계속돼 과금될 수 있다 (접수 로그의 job id 로 확인).
  - `wait_abandoned(timeout)` 으로 버려진 스레드가 끝나기를 기다릴 수 있다 (테스트·종료 정리용).
- OpenRouter 자체 제한(`FRAME_VIDEO_TIMEOUT_S`)과의 관계
  - 어댑터 제한은 접수부터 완료까지의 폴링 구간만 잰다. 범용 제한은 제출(최대 3회, 회당 60초 + 대기 60초 이하)·폴링·
    다운로드(회당 최대 300초)·정규화를 모두 감싼다.
  - 그래서 범용 제한이 더 길어야 한다. 권장 `FRAME_GENERATOR_TIMEOUT_S ≥ FRAME_VIDEO_TIMEOUT_S + 300` (기본값이면 900초).
    이러면 보통은 어댑터가 먼저 끊어 job id 가 담긴 `GENERATION_TIMEOUT` 을 남기고, 범용 제한은 다운로드·ffmpeg 가 멈춘 경우의 최후 방어선이 된다.
  - 더 짧게 두면 범용 제한이 먼저 끊어 job id 없는 메시지가 남고(접수 로그에는 있다), 버려진 스레드는 어댑터 제한까지 폴링을 계속한다.
    `FRAME_GENERATOR=external_api` 인데 범용 제한이 `FRAME_VIDEO_TIMEOUT_S` 보다 길지 않으면 기동 때 경고한다.
  - 범용 제한을 끄면(0) 어댑터 제한만 남는다. 다운로드·정규화가 멈추면 제한이 없다.

### 2. Decorator 체인 순서 (#1)
안쪽 → 바깥: 실제 생성기 → Retry → Timeout → Cache (각각 설정으로 켠다).
- Cache 가 가장 바깥: 적중하면 스레드·마감 없이 바로 돌려준다 (시간 제한을 거치지 않는다). 시간 초과·실패는 캐시에 들어가지 않는다.
- Timeout 이 Retry 바깥: 마감 하나가 장면 하나의 모든 시도를 감싸 총 시간의 상한이 된다. `GenerationTimeout` 은 Retry 밖에서 나므로
  재시도될 수 없다. `retryable=False` 이기도 해서 순서가 바뀌어도 재시도되지 않는다 (이중 장치).
- Timeout 을 Retry 안에 두지 않는다: 시도마다 마감이 따로 생겨 총 시간이 (재시도 + 1)배가 되고,
  끊긴 시도의 스레드가 도는 중에 다음 시도가 같은 장면을 다시 제출할 수 있다 (재과금).

### 3. 단계별 소요 시간 (#1)
- `PipelineContext.timings_ms` 에 끝난 단계뿐 아니라 실패한 단계의 소요 시간도 남긴다.
- FAILED 메시지: `[단계] 원인 · 진행: 장면 closeup (2/3) 에서 실패 · 소요: generate 1.2s, track 30ms`.
  `진행` 부분은 장면 단위로 일하는 단계(생성·추적)에서만 붙는다. 앞의 `[단계] 원인` 형식(ADR-0006)은 그대로다.

### 4. 실패 시 부분 결과 보존 (#3)
- 실패해도 그때까지 만든 장면 클립(부모에서 재사용한 장면 포함)·조립 영상(조립까지 끝났으면)·추적 결과·진행 상황을 버전에 남긴다.
  검수 결과는 남기지 않는다 (일부 검수기만 돈 판정은 지표를 왜곡한다).
- 장면 2 가 실패하면 장면 1 클립이 버전에 연결되어 `clip_uri`·`clip_url` 로 받을 수 있다. 실패한 장면은 `MEDIA_NOT_AVAILABLE`(404).
- 실패한 버전을 수정(`CreateRevision`)하면, 생성 단계의 부모 클립 재사용 규칙(fingerprint 가 같으면 재사용)으로
  끝난 장면을 다시 생성하지 않는다 (재과금 없음).
- 부분 결과 저장이 실패하면(예: 무결성 위반) 로그를 남기고 실패 상태만 다시 기록한다. 버전이 `PROCESSING` 에 묶이지 않게 한다.

### 5. 단계 진행 관찰과 저장 (#3)
- 값 객체 `PipelineProgress`/`StageProgress` (`domain/progress.py`). 시작한 단계만 순서대로 담고, 단계마다
  `status`(RUNNING·COMPLETED·FAILED)·`elapsed_ms`·`done`/`total`(장면 수)·`current`(처리 중이거나 실패한 장면)를 가진다.
- `PipelineStage.run` 이 단계 시작·완료·실패를 기록한다. 생성·추적 단계는 장면마다 `ctx.report_progress(끝낸 수, 전체, 지금 장면)` 으로
  알리고, 관찰자는 새 통지 `on_stage_progress` 로 받는다. 생성 단계는 모든 장면을 만든 뒤(조립 직전) 한 번 더 알린다.
- 유스케이스는 실행마다 진행 저장 관찰자를 붙여, 단계 시작·장면 진행·단계 완료 때 진행 상황과 끝난 장면 클립을 저장한다
  (버전이 `PROCESSING` 일 때만). 장면 하나가 수 분 걸리므로 장면마다 한 번 쓰는 비용은 무시할 만하다. 최종 결과는 유스케이스가 따로 기록한다.
- 저장은 `video_versions.progress_json` 컬럼 하나(JSON). API 는 `GET /api/versions/{id}` 의 `progress`
  (`stage`, `elapsed_ms`, `stages[]`). 실행 전 버전은 `null` 이다.
- `LoggingObserver`(logger `frame.pipeline`)가 시작·장면 진행·완료·실패를 로그로 남긴다. `build_container` 는 observers 를 주지 않으면 이것을 붙인다.
- 관찰자 오류는 로그만 남기고 파이프라인을 멈추지 않는다. 로그·진행 저장 같은 부수 기능이 유료 생성을 중단시키지 않게 한다
  (이전에는 관찰자 오류가 그 단계의 `UNEXPECTED_ERROR` 가 됐다).

## 결과
- 멈춘 생성이 작업 스레드를 무기한 묶지 않는다. 시간 초과는 `FAILED(GENERATION_TIMEOUT)` 으로 남고 다시 제출되지 않는다.
- 실패한 버전에서도 어느 단계·장면까지 무엇이 만들어졌는지 조회된다. 과금된 장면 결과를 받을 수 있고 수정 버전에서 재사용된다.
- 처리 중 화면이 `progress` 를 폴링해 "생성 2/3 (closeup)" 같은 진행을 보여 줄 수 있다.
- 비용과 한계
  - 버려진 스레드는 외부 작업·CPU 를 계속 쓴다 (강제 종료 불가). 늦게 끝난 결과는 버리고 로그로만 남는다.
  - FAILED 버전도 `video_url`·`clip_url` 을 가질 수 있다. 화면은 `state` 로 구분해야 한다.
  - DB 스키마에 `video_versions.progress_json`(널 허용)이 추가됐다. Alembic 이 없어 기존 DB 에는 이 열이 없다.
    `create_all` 은 기존 테이블에 열을 더하지 않으므로, 이 변경만으로는 기존 SQLite 파일(시연용 `frame.db` 등)을 다시 만들어야 한다.
    이슈 #2 작업이 `create_schema` 에서 빠진 널 허용 열을 더하게 하므로, 함께 합치면 SQLite 개발 DB 에는 자동으로 더해진다.
    MySQL 은 `ALTER TABLE video_versions ADD COLUMN progress_json TEXT NULL`.
  - 진행 저장은 버전 행을 읽어 다시 쓴다 (get → save). SQL 저장소는 바뀐 열만 UPDATE 하므로 그 사이 다른 곳이 바꾼 상태를
    되돌리지 않지만, 메모리 저장소는 객체 전체를 바꾼다. 처리 중인 버전을 다른 곳에서 동시에 쓰지 않는다고 가정한다.
- 남은 일: 늦게 끝난 결과를 실패 버전이나 캐시에 붙이는 훅, 어댑터가 버려진 작업을 알아채 폴링을 일찍 멈추는 신호(OpenRouter 폴링 루프),
  재시작 뒤 같은 버전을 이어 실행할 때 이미 만든 자기 클립 재사용(이슈 #2 와 함께), 진행 상황 푸시(SSE).

## 대안
- `signal.alarm`: 주 스레드·Unix 에서만 동작해 thread 디스패처(작업 스레드)·Windows 에서 쓸 수 없다. 기각.
- 프로세스로 격리해 강제 종료(multiprocessing): 어댑터·저장소·DB 연결을 넘겨야 하고, 강제 종료해도 외부 작업은 계속 과금된다. 기각.
- Timeout 을 Retry 안에: 위 2 참고. 기각.
- 시간 초과를 `retryable=True` 로: 접수된 작업의 재제출 = 재과금. 기각.
- 진행을 단계별 행(새 테이블)으로: 단계가 셋뿐이고 늘 버전과 함께 읽혀 JSON 컬럼 하나로 충분하다. 보류.
- 진행 전용 저장소 메서드(`update_progress`): 포트를 바꾸면 다른 작업(저장소 확장)과 겹치고, 기존 get/save 로 충분해 보류.
- 진행을 로그로만: 화면에서 볼 수 없어 기각 (`LoggingObserver` 는 운영 로그용으로 함께 둔다).

## 검증 (2026-11-12, 네트워크 없이)
- 느린 Fake 생성기(Event 로 막음) 계약 테스트: 마감에 돌아옴, `retryable=False`, 내부 오류 그대로 전달, 늦은 결과 로그,
  버려진 스레드의 재시도 차단, 정리 대기. 기존 생성기 계약 스위트에 `timeout(fake)` 조합 추가.
- 체인 순서(Cache → Timeout → Retry → 실제), `FRAME_GENERATOR_TIMEOUT_S` 해석, `external_api` 경고.
- 유스케이스: `FAILED(GENERATION_TIMEOUT)`·추적·검수 미실행·시간 초과 장면 재제출 없음·부모 보존,
  생성(장면 2)·추적(장면 2)·검수 단계 실패의 부분 결과, 처리 중 진행·클립 조회, 실패 버전 수정 시 끝난 장면 재사용,
  부분 결과 저장이 실패해도 FAILED 기록, 관찰자 오류 격리, 로그 내용.
- SQLite 왕복(`progress_json`, 부분 클립), API(TestClient): `progress`, 실패 버전의 장면 1 클립 200·장면 2 404 `MEDIA_NOT_AVAILABLE`.
- 실제 OpenRouter 호출과 서버 e2e 는 이 작업에서 돌리지 않았다 (통합 때 확인).
