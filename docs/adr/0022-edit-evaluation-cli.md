# ADR-0022 지표 1 평가 CLI: 인프로세스 실행과 측정 기준별 비대상 변경률

- 상태: 채택 (2026-11-14)
- 관련: T12, 성공 지표 1 (수정 성공률 ≥ 80%, 비대상 변경률 ≤ 10%), 이슈 #21, ADR-0013·0016·0017·0018·0020·0021
- 검토 기록: [docs/research/concept-ux-review.md](../research/concept-ux-review.md) §7 (B7, 결정 2)

## 맥락
- 지표 1 은 순수 함수(`evaluation/edit_metrics.py`)만 있었다. 실험을 실행·저장하는 도구가 없어 TDD_WORKFLOW §7 의 절차
  (컨테이너 조립 → `CreateRevision` → `AcceptGeneration` → `GET /checks` → `evaluate_cases`)를 손으로 해야 했다.
- 실제 검수기가 생겼다: 요구 반영 `vlm_requirement`, 유지 요소 `vlm_preservation`(`detail.method=vlm_keep`),
  SSIM `ssim_preservation`(`detail.method=ssim`). 수정 의도(kind·keep)도 생겼다 (ADR-0017·0018).
- 두 보존 검수기를 함께 켜면 `preservation_rate` 에 요소 판정(0/1)과 SSIM 값이 한 평균에 섞인다 (B7).
  지표 1 을 어느 쪽으로 잴지는 아직 정하지 않았다 (결정 2).
- 기존 판정 함수는 대상 요구조건을 `detail.predicate` 로 찾았다. `vlm_requirement` 가 장면 전체를 판정하지 못한 결과
  (ffmpeg·VLM 실패)에는 predicate 가 없다. 그 장면이 빠지고 나머지 장면의 PASS 만으로 '수정 성공' 이 될 수 있었다.

## 결정

### 1. 실행 방식: 인프로세스 (Container 를 직접 쓴다)
`python -m frame.tools.evaluate_edits --cases <cases.json> --out <result.json>` 는 서버 없이 FRAME_* 설정으로
컨테이너를 조립하고 유스케이스를 차례로 부른다.
- 케이스마다: 변경 계획(`PlanRevision`) → 수정 버전 생성(`CreateRevision`, 계획이 정한 kind·change·keep) →
  생성 접수(`AcceptGeneration`) → 완료 대기 → 검수 결과(`ProjectQueries.get_checks`).
- 계획을 먼저 보는 것은 화면의 순서(연구 문서 §4·F1)와 같다. 같은 프로세스라 생성기가 계획 때 받아 둔 부모 주소로
  편집한다 (ADR-0020). kind 를 생략한 케이스는 계획이 정한다 (지금 값과 같으면 REPAIR).
- 디스패처는 인라인으로 바꾼다 (`FRAME_JOBS` 무시). 완료 대기는 상태를 다시 읽는 루프라(`--timeout-s`, `--poll-s`)
  디스패처가 달라져도 동작한다.
- 모듈 (개념 하나에 모듈 하나): 케이스 파일 읽기 `evaluation/edit_cases.py` → 케이스 실행 `edit_runner.py`(실행 기록 `CaseRun`)
  → 판정·결과 파일 내용 `edit_report.py` → 지표 식·목표값 `edit_metrics.py`. 명령과 컨테이너 조립은 `tools/evaluate_edits.py` 가
  한다. `evaluation` 은 `domain`·`application` 만 import 하고, 실행기는 유스케이스 네 개를 `EditApp` 프로토콜로 받는다.
- 근거
  - 화면·API 가 부르는 것과 같은 유스케이스다. presentation 은 스키마 변환만 하므로 재는 동작이 같다.
  - 검수 결과를 도메인 객체(`CheckResult`)로 받아 `evaluate_cases` 에 바로 넣는다. HTTP 응답을 되돌리는 변환이 없다.
  - 서버 기동·포트·폴링이 필요 없고, Fake 어댑터로 테스트하기 쉽다 (`build_container(overrides=…)`).
  - 실행한 설정(생성기·검수기·모델·편집 모드)을 같은 `Settings` 에서 결과에 남긴다 (재현).
  - TDD_WORKFLOW §7 이 이미 이 절차(컨테이너 조립 → 유스케이스)를 적어 두었다.
- 대가
  - 부모 버전이 있는 DB·산출물 폴더를 가리켜야 한다 (`FRAME_PERSISTENCE=sqlalchemy`, `FRAME_DB_URL`,
    `FRAME_ARTIFACT_STORE=local_fs`, `FRAME_ARTIFACT_DIR`). `FRAME_PERSISTENCE=memory` 는 거부한다 (종료 코드 2).
  - 같은 DB 를 쓰는 서버는 끄고 돌린다. SQLite 는 쓰기를 하나씩 받고, 서버의 재시작 복구(ADR-0013)는 리스를 넘긴
    PROCESSING 을 워커 유실로 본다.
  - 서버의 설정이 아니라 CLI 를 띄운 환경 변수를 따른다. 생성기·검수기 설정을 서버와 같게 맞춘다 (README 예시).
  - 케이스를 하나씩 돈다 (20건이면 편집 약 40분, 새로 생성 약 110분).

### 2. 비대상 변경률은 측정 기준(`detail.method`)마다 따로 잰다
- 기준은 둘이다: `vlm_keep`(vlm_preservation 의 유지 요소 판정)와 `ssim`(ssim_preservation 의 부모 클립 대비 SSIM).
  결과에는 두 기준의 케이스별·전체 값과 목표 달성 여부를 모두 남긴다. `--preservation` 은 머리 판정
  (`summary.meets_target`)에 쓸 기준만 고른다. 기본은 `vlm_keep` 이고, 결정 2 가 정해지면 기본값만 바꾼다.
- 나누는 규칙 (`edit_metrics.select_preservation`)
  - 그 기준의 결과와, 부모 클립을 그대로 쓴 장면의 결과(`method=identical`)를 남긴다. 두 검수기가 재사용 장면마다
    하나씩 내므로 장면당 하나만 둔다.
  - 그 기준의 결과가 하나도 없으면(그 검수기를 켜지 않음) PRESERVATION 결과를 모두 뺀다 → 미판정.
    재사용 장면의 1.0 만 남아 다시 만든 장면을 보지 않고 '변경 없음' 이 되는 것을 막는다.
  - method 를 남기지 않는 검수기(Fake)의 결과는 어느 기준인지 알 수 없어 모든 기준에 넣는다. Fake 환경에서는 두 기준이 같다.
- 기본값을 `vlm_keep` 로 둔 까닭: 지표 1 의 정의가 "지정하지 않은 대상의 **속성** 변경 비율" 이고, ADR-0018 이
  vlm_preservation 을 지표 1 의 검수기로 두었다. SSIM 은 바꾼 대상의 변화까지 든 화면 전체 값이라 상한이다.

### 3. 대상 요구조건은 키로 찾는다
- 판정할 요구조건 = 수정 버전 IR 에서 다시 만든 장면의 (대상 객체, 술어) 요구조건의 키 (`EditCase.target_requirement_keys`).
- 키마다 결과가 있고 모두 PASS 여야 대상 반영(`target_met`)이다. 결과가 없거나 미판정이면 반영되지 않은 것이다.
- 키를 주지 않으면 예전처럼 `detail.predicate` 로 찾는다 (기존 호출과 호환).
- 케이스의 성공 = 대상 반영 그리고 그 기준의 비대상 변경률 ≤ 10% (기존 정의 그대로).

### 4. 케이스 파일과 결과 파일
- 케이스 파일(JSON): 필드 이름은 `POST /versions/{id}/revisions` 와 같다. 필수 `parent_version_id`·`predicate`·`new_value`,
  선택 `case_id`·`entity_key`(생략하면 장면 수준 조건)·`kind`·`keep`·`scope`·`scene_keys`·`current_value`·`note`.
  모르는 필드를 포함해 형식 오류는 실행 전에 모두 거른다 (오타 난 keep 이 조용히 빠지면 다른 조건으로 돈 실험이 된다).
  예시: [docs/evaluation/edit_cases.example.json](../evaluation/edit_cases.example.json).
- 결과 파일(JSON)
  - 실행 설정, 실제로 꽂힌 검수기 클래스, 목표값(`edit_metrics` 의 상수). 결과 파일은 저장소에 기록하므로(TDD_WORKFLOW §7)
    DB 주소·키 파일 경로·이 컴퓨터의 폴더는 남기지 않는다 (산출물 폴더는 종류 `artifact_store` 만, 케이스 파일은 이름만,
    오류 문구·검수 사유·경고에 섞인 절대 경로는 `<경로>` 로 가린다. 저장소 키 `local://` 와 웹 주소는 그대로).
  - `summary`: 건수(완료·실행 실패·오류 — 겹칠 수 있다), 머리 기준의 성공률·평균 비대상 변경률·목표 달성, 기준별 값(`by_basis`),
    끝나지 않은 수정 버전(`unfinished_versions`), 생성기가 기록한 영상 비용, 검수 VLM 호출 수·토큰.
  - `cases[]`: 입력, 계획(다시 만들 장면마다 편집 여부·어림값·편집하지 못하는 사유), 수정 버전 ID·상태·오류,
    대상 요구조건 키, 판정(머리 기준 `outcome`, 기준별 `by_basis`), 장면별 클립 URI(부모 클립 URI·재사용 여부·생성 기록),
    검수 결과 전부(detail·경고 구간).
- 케이스가 끝날 때마다 결과 파일을 다시 쓴다 (임시 파일에 쓰고 바꿔 끼운다). 중간에 끊기면 `complete: false`.
  쓰지 못하면(OSError) 경고하고 계속 간다 (기록은 메모리에 있어 다음 케이스 뒤에 다시 쓴다). 마지막까지 `--out` 에 못 쓰면
  같은 폴더의 `<이름>.fallback.json`, 그다음 임시 폴더에 쓰고 종료 코드 3 으로 알린다 (과금된 결과를 잃지 않게).
- `--out` 이 이미 있으면 아무것도 돌리지 않는다 (종료 코드 2, 덮어쓰려면 `--overwrite`). 같은 명령을 다시 치면 앞 결과가 첫
  저장에서 사라지고 끝난 케이스의 검수가 다시 과금되기 때문이다. 결과를 합치는 이어 하기(`--resume`)는 두지 않았다 (아래 대안).
- 케이스 하나의 실패(부모 없음·검증 오류·계획을 받지 못함·생성 실패·시간 초과·예상하지 못한 예외)는 그 케이스에 적고
  다음 케이스로 간다. 실패한 케이스도 분모에 든다 (성공으로 세지 않는다). 예상하지 못한 예외(서버를 켠 채 돌렸을 때의 SQLite 잠금 등)는
  `UNEXPECTED_ERROR`(생성 파이프라인과 같은 코드 이름)와 `예외 이름: 메시지` 로 적고 트레이스백은 로그로 낸다.
  한 케이스의 예상 밖 오류가 남은 유료 케이스를 막지 않게 하려는 것이다.
- 수정 버전을 만든 뒤 오류·Ctrl+C 로 끝나면 그 버전을 한 번 더 읽어 마지막 상태·클립·비용을 남긴다. 접수된 채(`QUEUED`·`PROCESSING`)
  남았으면 진행 줄·요약(`unfinished_versions`)에 알린다. 같은 DB 로 서버를 켜면 재시작 복구(ADR-0013)가 그 버전을 이어서 실행해
  남은 생성·검수가 과금될 수 있다 (같은 자리에서 되풀이되는 오류면 케이스 수만큼 남는다). 접수 전(`DRAFT`)은 다시 실행되지 않는다.
- Ctrl+C 로 멈추면 그때까지의 결과를 다시 쓰고, 하던 케이스와 그 케이스의 수정 버전 ID·상태를 `interrupted` 에 남긴 뒤
  종료 코드 130 으로 끝난다 (끝까지 돌면 `interrupted: null`). 끊긴 케이스는 판정·집계에 넣지 않는다. 하던 케이스와 시작하지 않은
  케이스만 담은 `<이름>.remaining.json` 을 남겨, 그것을 `--cases` 로·다른 파일을 `--out` 으로 이어 돌리게 한다.
- `--plan-only`: 계획만 보고 수정 버전을 만들지 않는다. 다시 만들 장면 수·편집 여부·어림 비용으로, 실험을 승인받기 전에
  비용을 적는다 (구조화된 변경이라 해석 LLM 을 부르지 않는다. 편집 모드면 부모 작업의 상태를 조회한다).

## 비용 (실제 모델로 20건)

| 항목 | 단가 (480p·4초 장면, 연구 문서 §6.1 실측) |
|---|---|
| 다시 만드는 장면 — 부모 클립 편집 (FLUX Video Edit) | $0.12 |
| 다시 만드는 장면 — 새로 생성 (Wan 3.0) | $0.2125 |
| 재검수 (gpt-4.1, 다시 만든 장면의 요구 반영·유지 요소, 일관성 포함. SSIM 은 과금 없음) | 수정 한 번에 약 $0.04~0.05 |
| 변경 계획 | $0 (LLM 호출 없음) |

- 케이스 하나 = 다시 만드는 장면 수 × 영상 단가 + 재검수. 장면 2개면 편집 약 $0.29, 새로 생성 약 $0.48.
- 20건: 장면 2개씩이면 편집 약 $5.8 ~ 새로 생성 약 $9.5. 장면 1~3개가 섞이면 약 $3.4 ~ $13.8.
- 원본이 없으면 따로 든다: 4장면·16초 원본 하나에 약 $0.85 + 검수 약 $0.05 (원본 4개면 약 $3.6).
- 시간: 수정 한 번에 편집 약 2분, 새로 생성 약 5분 35초 → 20건 약 40~110분.
- 같은 케이스를 다시 돌리면 생성 캐시(ADR-0013)가 같은 조건의 장면을 다시 쓴다 (임시 결과는 제외, ADR-0021). 검수는 다시 돈다.
- 원본을 만든 직후에는 부모 저장본 주소가 늦게 나와 편집 대신 새로 생성될 수 있다 (연구 문서 §6.3). 원본은 미리 만들어 두고
  `--plan-only` 의 `edit_scene_count`·`edit_unavailable_reason` 으로 편집이 될지 먼저 본다.

## 한계
- VLM 한 번의 판정이다 (ADR-0018). 같은 클립을 다시 물으면 흔들릴 수 있다.
- `preservation_rate` 는 미판정이 하나라도 섞이면 점수 평균 대신 PASS 비율로 계산한다 (`domain/metrics.py`).
  SSIM 기준에서 한 장면이 미판정이면 값이 크게 뛴다.
- 비대상 변경률은 수정 버전의 모든 장면(재사용 장면 1.0 포함) 평균이다. 장면이 많은 영상일수록 낮게 나온다.
  다시 만든 장면만의 값은 결과의 `checks` 로 다시 계산할 수 있다.
- SSIM 은 바꾼 대상의 변화까지 들어 있다 (마스크 SSIM 은 실제 추적기 뒤, AC-34).
- 생성 캐시에서 다시 쓴 클립의 `recorded_cost_usd` 는 처음 만들 때의 값이다 (이번 실행에 실제로 쓴 돈보다 클 수 있다).
- 경로 가리기는 글자 모양으로 찾는다. 따옴표 없이 공백이 든 경로는 공백 뒤에 경로 구분자가 더 있을 때만 이어서 가린다
  (`…\clip 1.mp4` 의 ` 1.mp4` 처럼 마지막 토막이 남을 수 있다). 기록하기 전에 한 번 훑어본다.
- `FRAME_GENERATOR_TIMEOUT_S` 를 켜면 Windows 에서는 Ctrl+C 가 그 장면의 생성이 끝날(또는 제한에 닿을) 때까지 늦게 먹을 수 있다
  (시간 제한 데코레이터가 이벤트를 기다리는 동안 SIGINT 가 미뤄진다. 기본 설정에서는 곧바로 멈춘다).

## 대안
- 띄운 서버의 HTTP API: 화면과 같은 서버 설정과 presentation 까지 지나고, 서버를 끄지 않아도 된다.
  대신 서버 기동·폴링이 필요하고, 응답 JSON 을 `CheckResult` 로 되돌려야 하며, 서버의 생성기·검수기 설정은 `/health` 의 키로만 보인다.
  테스트에도 TestClient 를 끼울 어댑터가 더 든다. 실행기가 받는 것은 유스케이스 네 개(`EditApp` 프로토콜)라서,
  같은 모양의 HTTP 구현을 붙이면 나중에 옮길 수 있다.
- 기준을 하나로 정해 그것만 기록: 결정 2 가 열려 있다. 두 기준을 함께 남기면 같은 실행 한 번으로 둘 다 본다.
- 검수기마다 결과에 검수기 키를 붙여 나누기: 정확하지만 저장 형식이 바뀌고 서버가 만든 결과에는 없다.
  `method` 는 이미 저장되고, ADR-0018 이 함께 켤 때는 method 로 나눠 보라고 적었다.
- 이어 하기(`--resume`, 앞 결과 파일을 읽어 끝난 케이스를 건너뛰고 결과를 합침): 판정을 다시 하려면 JSON 에서 검수 결과를
  되살려야 하고 VLM 판정은 같은 클립에도 흔들린다. 남은 케이스 파일과 다른 `--out` 으로 충분한지 20건 실행에서 보고 정한다.
- 앞 결과 파일을 `.bak` 으로 옮기고 덮어쓰기: 실수로 다시 친 명령이 끝난 케이스의 검수를 다시 과금하는 것은 막지 못한다.

## 검증
- 단위: `tests/unit/test_edit_metrics.py`(키로 찾기·기준별 나누기·목표 상수), `tests/unit/test_edit_cases.py`(케이스 파일),
  `tests/unit/test_edit_report.py`(결과 파일의 경로 가리기),
  `tests/unit/test_evaluate_edits_tool.py`(Fake 컨테이너로 CLI: 판정·근거·기준 옵션·REPAIR 와 keep·실패한 케이스·
  예상 밖 오류·계획을 받지 못한 케이스·끝나지 않은 수정 버전 알림·Ctrl+C 와 종료 코드 130·남은 케이스 파일·케이스마다 저장·
  쓰기 실패와 대체 파일(종료 코드 3)·덮어쓰기 막기·계획만·로컬 폴더 경로 없음·도움말의 목표값·완료 대기와 시간 초과).
- 통합: `tests/integration/test_evaluate_edits_cli.py` (환경 변수로 SQLite 파일 DB·local_fs 를 가리켜 서버가 만든 원본을 평가).
- 실제 모델: `tests/live/test_evaluate_edits_live.py` (`slow`, `FRAME_EVAL_CASES` 를 줄 때만). 비용 승인 전이라 아직 돌리지 않았다.
