# 백엔드 감사 보고서 (2026-11)

- 감사 시점: 2026-11-14. 기준 커밋 `feature-US03-concept-ux` `2dd3f2e` (dev `d94cf58` 위 29커밋).
- 범위: 백엔드 전체 — `src/frame`(17,033줄), `tests/`(15,381줄), `pyproject.toml`, `docker/`, `.github/`, `scripts/`,
  설계 문서·ADR 0001~0021. `frontend/` 는 대상이 아니다.
- 작업 중이던 네 브랜치(#21, #10·#11·#12, #17, 외부 숏폼 연구)는 감사 시점의 커밋과 작업 중 변경을 읽었다.
  그 결과는 세션별 전달 노트에 있고, 이 문서에는 요약과 ID 만 싣는다 (§11).
- 방법: 도메인·애플리케이션·설정·프레젠테이션·영속성·저장소 계층은 전부 직접 읽었다. 생성기·검수기·LLM·구조화 어댑터, 도구·CI,
  테스트, 문서는 영역별로 나눠 읽었다. 높음·중간 발견은 해당 줄을 다시 읽거나 읽기 전용 실행으로 재현해 확인했고 ('근거' 에 '재현'·'확인' 으로 적었다),
  낮음 발견 가운데 일부는 영역별 감사의 근거를 그대로 실었다. 확인하지 못한 것은 '추정' 이라고 적었다.
- 기준: 정확성·보안, 계층 규칙, 모듈 구조, 재사용과 패턴, 클린코드, 테스트, CI 에 더해 **개념 경계와 인지 부담** (모듈을 개념 단위로 나눠
  뒤에 들어오는 사람이 읽을 양을 줄이는가)으로 보았다 (§5.1).
- 이 문서는 발견과 계획의 기록이다. 고친 것은 '상태' 열에 커밋과 함께 갱신한다.

## 1. 요약

완료된 요소(dev + concept-ux)의 발견은 **110건 — 높음 3 · 중간 54 · 낮음 53** 다.
작업 중인 네 브랜치에 남은 발견은 46건(높음 2 · 중간 16 · 낮음 28)이고 세션별 전달 노트에 있다 (§11).

| 영역 | 높음 | 중간 | 낮음 | 절 |
|---|---|---|---|---|
| 정확성·보안 (A) | 1 | 21 | 19 | §3 |
| 계층 규칙 (L) | 0 | 1 | 3 | §4 |
| 개념 경계·인지 부담 (K) | 0 | 4 | 1 | §5.1 |
| 모듈 구조 (M) | 0 | 4 | 3 | §5.2 |
| 재사용·패턴 (R) | 0 | 4 | 6 | §6 |
| 클린코드 (C) | 0 | 0 | 10 | §7, §10 |
| 테스트 (T) | 1 | 7 | 3 | §8 |
| CI·운영 (O) | 1 | 9 | 4 | §9 |
| 문서 (D) | 0 | 4 | 4 | §10 |

높음 세 건
- **A-01** 객체·장면 키의 형식을 어디서도 검사하지 않는다. 키가 저장 경로에 그대로 들어가, `../` 가 든 장면 키로 저장 루트 안의 다른 버전 클립을
  덮어쓸 수 있다 (재현). IR 직접 입력(#10·#11)이 들어오면 사용자가 키를 직접 준다.
- **T-02** 문서·Dockerfile 이 권하는 실행 방식인 스레드 디스패처에 테스트가 하나도 없다. 그 조합에서 결함이 재현됐다:
  스레드 디스패처 + SQLAlchemy 기본 주소(in-memory SQLite)에서 버전이 처리 중에 멈춘다 (A-41, 8회 중 3회만 완료).
- **O-01** 릴리스 번들이 테스트 게이트 없이, 고정되지 않은 의존성으로 만들어진다 (태그 push 는 `ci.yml` 을 돌리지 않는다).

그다음으로 볼 것
- **유료 경로**: 일상 경로에서 반드시 재과금되거나 키·서명 URL 이 새는 경로는 없다. 조건이 맞을 때의 경로가 여섯 건 있다 —
  원장을 제자리 덮어쓰기로 쓴다(A-09), 클립이 남아 있는 완료 기록에도 24시간 기한을 적용한다(A-32), REPAIR 가 고치려는 요구조건이 결과 키에 없어
  다른 수정의 클립을 받는다(A-33), 시간 제한으로 버려진 스레드가 마감 뒤에 제출한다(A-34), 편집 거절 뒤 진행 중인 같은 작업을 두고 새로 제출한다(A-35),
  편집 모델의 사전 확인 실패가 새로 생성으로 넘어가지 않는다(A-36).
- **검수 판정**: 검수기 하나의 예상 밖 예외가 유료 생성이 끝난 버전을 FAILED 로 만든다(A-13). 로컬 저장소에서 외부 URL 사용자 참조가 있는 객체는
  일관성 검수가 늘 미판정이다(A-21). 일관성 검수가 응답의 빈 곳을 통과 쪽으로 읽는다(A-22). 부모의 미판정까지 물려받는다(A-23).
- **CI**: 지금 게이트는 lint 일부와 pytest 뿐이고, CI 가 ffmpeg 를 설치하지 않아 실제 어댑터 테스트 약 94개가 건너뛰어지는 것으로 보인다(T-03, 추정).
  기본 `pytest` 는 유료 테스트를 수집한다(T-01).
- **개념 경계**: 해시·키 개념 일곱 가지가 비슷한 이름으로 흩어져 있고(K-01), '수정'·'계획'·'참조'·'보존' 같은 낱말이 여러 개념을 가리키고(K-02),
  패키지의 입구와 모듈 지도가 없고(K-03), 생성기 패키지 하나에 네 개념이 섞여 있다(K-04).
  개념 지도(개념 → 지금 있는 곳 → 걸리는 것), 목표 배치, 배치 규칙은 §5.1 에 있다.
- **구조**: `openrouter.py` 는 1,675줄에 클래스 하나가 메서드 43개다(M-01). VLM 검수기 셋과 정체성 추출기가 같은 흐름을 네 번 쓰고 이미 서로
  어긋나 있다(M-04). `default_registry()` 는 250줄이다(M-02). 계층 방향 위반은 0건이지만 규칙을 지키는 검사가 없다(L-01).

계획은 §12 에 있다. 2단계(CI 게이트)를 먼저 하고, 3단계에서 다른 브랜치가 건드리지 않은 파일의 발견을 고친다.
다른 브랜치가 고치고 있는 파일의 발견 16묶음은 그 브랜치가 dev 에 들어간 뒤로 미룬다 (§12.2). 결정이 필요한 것은 일곱 가지였고,
그중 개발 도구 설치(mypy)는 승인을 받아 설치했다. 여섯 가지가 남았다 (§12.3).

## 2. 기준선 (감사 시점의 측정값)

| 항목 | 값 | 명령 |
|---|---|---|
| 테스트 | 982 통과 · 3 건너뜀 · 2 제외, 65.8초 | `pytest -q -p no:cacheprovider -m "not slow and not gpu"` |
| 커버리지 (분기 포함) | 전체 90.28% | `--cov=frame` |
| 계층별 커버리지 | domain 98.5 · application 97.1 · adapters 93.8 · presentation 98.1 · config 98.4 · evaluation 100 · tools 51.7 | `coverage report --include` |
| `ruff check` (E·F·I·B·UP) | 통과 | `ruff check src tests` |
| `ruff format --check` | 8파일 미적용 (C-01) | `ruff format --check src tests` |
| 계층 간 import | 위반 0건 (계층 간 간선 14종, 모두 허용 방향) | AST 스캔 (§4) |
| 바로 켤 수 있는 ruff 규칙군 (src·tests 위반 0) | PIE · RET · DTZ · A · ERA · PGH · PERF · LOG · G · ASYNC · EXE · ICN · INP · RSE · SLOT (15개) | `ruff check --select <군>` |
| 몇 건 고치면 켤 수 있는 규칙군 | BLE 1 · SIM 3 · C4 1 · PTH 1 · FURB 1 · ISC 1 (모두 8건) | 같음 |
| 기준선이 필요한 규칙군 | S: src 32 (그중 `assert` 11), tests 2,203 (그중 `assert` 2,196 — 파일별 예외) · C90: src 12 (최대 복잡도 19) · T20: src 15 (도구), tests 7 (live) | 같음 |
| 로컬 실행 환경 | Python 3.11.0rc2, ruff 0.17.4, pytest 9.2.0, mypy 없음 (감사 시점) | 공유 `.venv` |
| 타입 검사 (감사 뒤 측정. mypy 2.4.1 을 사용자 승인으로 설치, 2026-11-14) | 지금 설정(`strict = false`): 오류 24건 · 10파일 (137파일 검사) — adapters 15 · tools 6 · config 2 · application 1, domain·presentation·evaluation 0. `--strict`: 42건 · 16파일 (domain 0) | `mypy src`, `mypy src --strict` |

코드의 바탕은 좋다. 계층 방향 위반이 없고, `TODO`·`FIXME` 가 없고, 넓은 `except` 20곳에는 모두 이유가 적혀 있고,
외부 호출 어댑터는 가짜 전송으로 테스트된다. 아래 발견은 그 위에서 남은 것이다.

## 3. 정확성·보안

담당 표기: **감사** = 이 감사·CI 세션 / **#10** = #10·#11·#12 세션 / **결정** = 사용자·오케스트레이터 결정이 먼저 필요.
시점 표기: **지금** = 3단계에서 바로 (다른 브랜치가 건드리지 않은 파일) / **머지 뒤** = 그 파일을 고치고 있는 브랜치가 dev 에 들어간 뒤.

### A-01 · 높음 · 객체·장면 키의 형식을 검사하지 않는다
- 어디: `src/frame/domain/ir.py:123-140` (`validate` 는 유일성과 참조만 본다), `src/frame/adapters/structurer/rule_based.py:29-33`
  (DSL 이 `\S+` 를 그대로 키로 쓴다), `src/frame/adapters/structurer/llm_schema.py:58,68` (스키마는 설명으로만 "snake_case" 를 요구한다).
- 근거 (재현): 기본 설정(`FRAME_STRUCTURER=rule_based`)에서 `scene ../ver_victim/intro 0-1000` 이 구조화·검증을 통과한다.
  클립 저장 키가 `clips/{version_id}/{scene_key}.mp4` 라서 (`adapters/generator/openrouter.py:1198`, `fake.py:90`)
  `LocalFileArtifactStore` 가 저장 루트 **안**의 다른 버전 클립을 덮어쓴다 — `local://clips/ver_attacker/../ver_victim/intro.mp4` 로
  실제로 덮어써졌다. 루트 밖은 `adapters/storage/local_fs.py:52-56` 이 막는다.
  그 밖에: 객체 키 `-` 는 장면 수준 조건의 요구조건 키와 충돌한다 (`s:-:color`, `domain/ir.py:55-58`), `:` 는 키 경계를 흐린다,
  `/` 가 든 키는 `GET /versions/{id}/scenes/{scene_key}/clip` 에서 찾을 수 없다, 길이 제한이 없어 `scenes.scene_id` `String(160)`·
  `requirements.requirement_id` `String(320)` 을 넘는다 (`adapters/persistence/sqlalchemy/models.py:79,127`).
- 영향: 인증이 없는 API 에서 다른 버전의 산출물을 덮어쓸 수 있다 (버전 ID 를 알아야 한다). 시연 범위에서는 위험이 낮지만,
  IR 직접 입력(#10·#11)이 들어오면 사용자가 키를 직접 준다.
- 제안: (1) 도메인에서 한 번 막는다 — `IntermediateRepresentation.validate()` 가 모든 `entity_key`·`scene_key` 에
  `[A-Za-z0-9][A-Za-z0-9_]{0,63}` 을 요구한다. 저장소의 기존 키는 모두 이 규칙 안이다 (grep 으로 확인).
  (2) 저장소 방어 — `LocalFileArtifactStore` 가 `..` 조각이나 절대 경로가 든 키를 거부한다.
- #10 브랜치(`bb1a0e8`)에서도 재현된다: 블록 입력 `POST /api/projects {ir}` 에 장면 키 `../ver_victim/intro`·`a/b`, 객체 키 `a:b`·`-`·`컵` 을
  넣으면 다섯 가지 모두 201 DRAFT 로 저장된다 (스키마의 제약은 길이 64자뿐이다).
- 담당·시점: (1) **#10** (그 브랜치가 `domain/ir.py` 를 고치고 있다. 전달 노트 1번. 하지 않으면 감사가 머지 뒤 P-01),
  (2) **감사 · 지금**.
- 상태: **일부 해결** — (2) 저장소 쪽은 `11e06e2` (포트에 저장 키 규칙 `check_artifact_key`, 두 구현이 따른다. `..`·빈 조각·역슬래시·콜론·제어 문자·점이나 공백으로 끝나는 조각을 받지 않는다). (1) 도메인 규칙은 #10 에 전달

### A-02 · 중간 · 구조화기가 LLM 이 아니면 화면 키 입력이 VLM·해석기에 닿지 않는다
- 어디: `src/frame/config/container.py:153-156` (`uses_openai = settings.structurer == "llm" and ...`),
  `src/frame/config/registry.py:127` (`key_source=ctx.llm_keys or LLMKeyStore(...)`), `src/frame/presentation/routers/settings.py:36-39`.
- 근거 (재현): `structurer=rule_based`, `checkers=(vlm_requirement,)`, `change_interpreter=llm`, `llm_runtime_key=True` 로 조립하면
  `container.llm_keys` 가 `None` 이다. 검수기와 해석기의 LLM 클라이언트가 **서로 다른** 키 저장소를 하나씩 갖고,
  `GET`·`PUT /api/settings/llm-key` 는 404, `/health` 의 `llm_key` 는 `null` 이다.
- 영향: 그 조합에서 화면으로 키를 넣을 방법이 없다. 키 없이 기동하도록 켜 둔 상태라 VLM 검수가 전부 미판정이 된다.
  시연 프리셋에 실제 검수기를 넣으려는 계획(이슈 현황의 B11)이 여기에 걸린다. `/health` 의 `llm_provider`·`llm_model` 도 같은 조건이라 보이지 않는다.
- 제안: "LLM 클라이언트를 쓰는 구성 요소가 하나라도 있는가" 를 한 함수로 판단한다 (구조화기 `llm`, 해석기 `llm`, 정체성 `vlm_crop`,
  검수기 `vlm_*`). 그때 키 저장소를 하나 만들어 모든 클라이언트가 공유한다. 테스트를 먼저 쓴다 (동작이 바뀐다).
- 담당·시점: **감사 · 머지 뒤** (`config/container.py` 를 #10 브랜치가 고치고 있다. P-02)
- 상태: 열림

### A-03 · 중간 · 환경 변수 읽기가 항목마다 다르고 범위를 검사하지 않는다
- 어디: `src/frame/config/settings.py:98-188`.
- 근거 (재현): 빈 값의 처리가 세 가지다. `FRAME_VIDEO_FPS=` 는 기본값, `FRAME_STRUCTURER=` 는 빈 문자열 그대로(뒤에서 등록 오류),
  `FRAME_GENERATOR_RETRIES=`·`FRAME_LLM_TIMEOUT_S=`·`FRAME_LLM_MAX_ATTEMPTS=`·`FRAME_RATE_LIMIT_PER_MINUTE=` 는
  `ValueError: invalid literal for int() with base 10: ''` 로 기동이 죽는다 (변수 이름이 메시지에 없다).
  `FRAME_CHECKERS=` 는 검수기 0개로 조용히 돈다. 범위 밖 값은 받아들인다: `FRAME_CONSISTENCY_THRESHOLD=1.5`, `FRAME_REQUIREMENT_FRAMES=0`,
  `FRAME_VIDEO_FPS=-1`. 기본값도 두 군데에 적힌 것이 있다 (`"0"` 리터럴과 `base.` 값).
  기본값이 켜짐인 플래그 셋(`FRAME_GENERATOR_CACHE`·`FRAME_IDENTITY_VERIFY_ATTRIBUTES`·`FRAME_LLM_STRICT_SCHEMA`)은 빈 값이면 조용히 꺼진다
  (`settings.py:106-108` — 값이 있기만 하면 기본값을 쓰지 않고 `"" in {"1", "true", …}` 로 판정한다).
- 영향: `.env` 를 손으로 고치다 빈 줄을 남기면 원인을 알기 어려운 오류로 죽거나, 틀린 값으로 유료 생성까지 간다.
- 제안: 형식별 읽기 함수 하나씩(`_text`·`_int`·`_float`·`_flag`·`_list`)으로 통일한다. 빈 값은 늘 기본값, 잘못된 값은
  `ConfigurationError("FRAME_X: '값' 은 정수가 아닙니다")`, 범위는 `Settings.__post_init__` 에서 한 번에 본다. 필드 이름은 바꾸지 않는다.
- 담당·시점: **감사 · 머지 뒤** (`settings.py` 를 #10 브랜치가 고치고 있다. P-03)
- 상태: 열림

### A-04 · 낮음 · CORS 가 `DELETE` 사전 요청을 거부한다
- 어디: `src/frame/presentation/app.py:56` `allow_methods=["GET", "POST", "PUT", "OPTIONS"]`.
- 근거 (재현): `FRAME_CORS_ORIGINS` 를 켜고 `DELETE /api/settings/llm-key` 의 사전 요청을 보내면 400 `Disallowed CORS method` 다.
  그 경로는 실제로 있다 (`routers/settings.py:90`).
- 제안: 목록에 `"DELETE"` 를 더한다. 사전 요청 테스트 한 건.
- 담당·시점: **#10** (그 브랜치가 업로드 라우터를 더하며 `app.py` 를 고쳤다. 전달 노트 11번. `bb1a0e8` 에서도 재현된다. 하지 않으면 감사가 머지 뒤 P-14) · 상태: 전달

### A-05 · 중간 · 구조화 유스케이스가 저장 단위를 연 채 LLM 을 부르고, 예상 밖 오류를 기록 없이 삼킨다
- 어디: `src/frame/application/use_cases/structure_requirements.py:23-57`.
- 근거: `with self._uow_factory() as uow:` 안에서 `self._structurer.structure(request)` 를 부른다 (40행). LLM 응답을 기다리는 수십 초 동안
  DB 연결을 쥔다. `RunGenerationPipeline` 은 단계를 돌리기 전에 커밋하고 닫는다 (`run_generation_pipeline.py:59-81`) — 같은 원칙이 여기만 빠졌다.
  53~54행 `except Exception` 은 어떤 예외든 `getattr(exc, "code", "STRUCTURING_ERROR")` 로 FAILED 에 적고 **로그를 남기지 않는다**.
  코드 결함(`KeyError` 등)도 사용자 오류처럼 보이고 서버 로그에는 흔적이 없다. 파이프라인은 같은 상황을 `UNEXPECTED_ERROR` 로 적는다
  (`application/pipeline/runner.py:45-48`).
- 제안: 읽기 → 닫기 → 구조화 → 다시 열어 조건부 저장(`try_transition(STRUCTURING → DRAFT)`)의 세 토막으로 나눈다.
  실패 정규화는 파이프라인과 같은 함수를 쓴다 (R-09): `AdapterError`·`DomainError` 는 그 코드, 나머지는 `UNEXPECTED_ERROR` + `log.exception`.
- 담당·시점: **감사 · 머지 뒤** (#10 브랜치가 이 파일을 고쳤다. P-05) · 상태: 열림

### A-06 · 중간 · 워커 리스에 하트비트가 없다
- 어디: `src/frame/application/use_cases/recover_pending_versions.py:146-149` (`started_at` 만 본다), `config/settings.py:55` (리스 1800초).
- 근거: 주기 검사는 `now - started_at > lease` 인 PROCESSING 을 워커 유실로 본다. 버전 하나가 1800초를 넘게 돌면 (4장면 실측 613초,
  장면이 12개쯤이면 넘는다) **살아 있는 워커**의 버전을 QUEUED 로 되돌려 한 번 더 실행한다. 생성 캐시·작업 원장·키별 잠금이 재과금은 막지만,
  두 실행이 같은 버전에 진행·결과를 번갈아 쓴다. 설정 주석(53행)이 "리스는 최대 처리 시간보다 길게" 라고 사용자에게 맡기고 있다.
- 제안: 진행 기록 관찰자(`_ProgressWriter`)가 장면마다 저장할 때 `heartbeat_at` 을 함께 쓰고, 리스는 `max(started_at, heartbeat_at)` 에서 잰다.
  열이 하나 늘어난다 (스키마 변경). 오케스트레이터 노트의 후속 후보 "리스 하트비트" 와 같은 항목이다.
- 담당·시점: **결정** (스키마 변경 → 이슈로 올릴지) · 상태: 열림

### A-07 · 낮음 · 스레드 디스패처가 끝난 작업을 계속 쥐고, 워커 수가 고정이다
- 어디: `src/frame/adapters/jobs/thread.py:14-21`. `self.futures.append(future)` 로 모든 `Future` 가 프로세스가 끝날 때까지 남는다.
  `max_workers=2` 는 설정으로 바꿀 수 없다. 서버 종료 때 `shutdown()` 을 부르는 곳이 없다 (`presentation/app.py:89-103` 은 복구 스레드만 멈춘다).
- 제안: 끝난 `Future` 는 목록에서 뺀다 (`add_done_callback`). `wait()` 는 그대로 동작하게 한다. 워커 수는 생성자 인자로 두고 설정 연결은 A-03 과 함께.
- 담당·시점: **감사 · 지금** · 상태: 열림

### A-08 · 낮음 · 요청 제한기의 IP 별 기록이 지워지지 않는다
- 어디: `src/frame/presentation/rate_limit.py:26-39`. `self._hits[key]` 는 창이 비어도 키가 남는다. 공개 시연에서 IP 수만큼 늘어난다.
- 제안: 창이 비면 키를 지운다.
- 담당·시점: **감사 · 머지 뒤** (#10 브랜치가 이 파일의 주석을 고쳤다. P-10) · 상태: 열림

### A-09 · 중간 · 원장·캐시·클립을 제자리 덮어쓰기로 쓴다
- 어디: `src/frame/adapters/storage/local_fs.py:21-25` `path.write_bytes(data)` (열면서 0바이트로 자른 뒤 쓴다),
  쓰는 쪽 `adapters/generator/ledger.py:137-139`·`cache.py:138-139`, 읽는 쪽 `openrouter.py:1321-1327` (읽지 못하면 없는 것으로 보고 새로 제출).
- 근거: (a) 접수 기록(`submitted`)을 완료 기록으로 덮어쓰는 도중 프로세스가 끊기거나 디스크가 차면 0바이트·잘린 JSON 이 남는다.
  다음 요청은 "읽을 수 없음 → 없음" 으로 보고 다시 제출한다. 깨진 항목을 없는 것으로 보는 정책은 ADR-0013 의 의도지만,
  깨진 항목을 만들 수 있는 쓰기 방식은 문서에 없다. (b) 편집 요청 스레드는 조건 키를 잠금 없이 읽고 쓴다 (`openrouter.py:1283-1297`).
  같은 조건을 편집 없이 만드는 스레드와 겹치면 상대의 접수 기록을 덮어쓸 수 있다. (c) 잘린 클립도 `exists()` 는 참이라
  캐시가 계속 적중한다 (`cache.py:118`).
- 제안: 같은 폴더의 임시 파일에 쓴 뒤 `os.replace` 로 바꿔 끼운다 (Windows 에서 읽는 쪽이 열고 있으면 짧게 다시 시도).
  조건 키 접근은 조건 키 잠금 안에서 한다 (잠금 순서는 지금처럼 편집 키 → 조건 키 한 방향이다).
- 담당·시점: **감사 · 지금** (A-01 의 저장소 방어와 같은 커밋 묶음) · 상태: **해결** (`a2be59c` — 임시 파일에 쓰고 fsync 한 뒤 바꿔 끼운다. 덮어쓰는 동안 읽으면 빈 내용이 보이던 것을 테스트로 재현하고 고쳤다.
  (b) `4d56bbc` — 조건 키는 그 키의 잠금을 얻었을 때만 읽고 쓴다 (`KeyedLocks.try_hold`). 제안은 "잠금 안에서" 였는데, 상대가 그 잠금을
  몇 분씩 쥐고 있을 수 있어 기다리지 않고 물러나게 했다 — 기다리면 다 만든 클립이 범용 시간 제한에 걸려 버려질 수 있다.
  편집 없는 요청이 제출 응답을 기다리는 동안 편집 요청이 조건 키에 결과를 쓰던 것을 테스트로 재현했다)

### A-10 · 낮음 · 규칙 기반 DSL 이 `ANIMAL` 종류를 받지 못한다
- 어디: `src/frame/adapters/structurer/rule_based.py:30` — 종류 목록 `(PERSON|PROP|BACKGROUND|TEXT|OTHER)` 를 정규식에 손으로 적었다.
- 근거 (재현): `entity dog ANIMAL: 강아지` → `StructuringError: 해석할 수 없는 문장`. `EntityKind.ANIMAL` 은 ADR-0008 에서 더해졌다.
- 제안: 목록을 `EntityKind` 에서 만든다 (`"|".join(EntityKind)`). 테스트 한 건.
- 담당·시점: **감사 · 지금** · 상태: 열림

### A-11 · 낮음 · '같은 기기' 판정이 프록시 뒤에서 뜻이 달라진다
- 어디: `src/frame/presentation/routers/settings.py:42-47` — `request.client.host in LOOPBACK_HOSTS`.
- 근거: 같은 기기의 역방향 프록시(nginx 등) 뒤에서 `--proxy-headers` 없이 띄우면 모든 요청의 주소가 `127.0.0.1` 이다.
  그러면 원격 사용자도 키를 넣을 수 있다. 요청 제한 모듈은 이 조건을 docstring 에 적었는데 (`rate_limit.py:3-4`) 키 입력 쪽에는 없다.
- 제안: `routers/settings.py` docstring 과 `docs/DEMO.md` 에 같은 조건을 적는다. 코드 변경은 없다.
- 담당·시점: **감사 · 지금** (docstring) / 머지 뒤 (문서) · 상태: 열림

### A-12 · 낮음 · `VersionRepository.save` 의 동시 갱신 의미가 계약에 없다
- 어디: `src/frame/application/ports/repositories.py:36-38`, 구현 `adapters/persistence/sqlalchemy/repositories.py:97-111`·`memory.py:73-76`.
- 근거: 유스케이스는 읽고 → 고치고 → `save` 한다. SQL 구현은 ORM 이 바뀐 열만 UPDATE 하므로 다른 트랜잭션의 상태 변경을 대개 덮어쓰지 않는다.
  메모리 구현은 객체 전체를 덮어쓴다. 두 구현의 차이가 계약 테스트에 없다. #10 브랜치는 이 때문에 `save` 대신 조건부 갱신
  `replace_draft_ir` 을 따로 만들었다 (그 브랜치 ADR 의 '대안' 절).
- 제안: 포트 docstring 에 "상태 전이는 `try_transition` 으로만, `save` 는 읽은 뒤 바뀐 필드를 쓴다" 를 적고,
  계약 테스트에 '읽은 뒤 다른 저장 단위가 상태를 바꿨을 때' 를 더한다. 이슈 #4(동시 접수 경쟁 테스트)와 함께 하는 것이 맞다.
- 담당·시점: **감사 · 머지 뒤** (#10 브랜치가 포트·구현·계약 테스트를 고치고 있다) · 상태: 열림

### A-13 · 중간 · 검수기 하나의 예외가 검수 전체와 버전을 실패로 만든다
- 어디: `src/frame/application/checks.py:20-24`, `application/pipeline/stages.py:289`,
  새는 곳 `adapters/llm/openai_client.py:165-178`, `adapters/checker/vlm_consistency.py:167`.
- 근거: `CheckSuite.run` 은 검수기를 차례로 부를 뿐이고, 검수기는 `AdapterError` 만 미판정으로 바꾼다. 그 밖의 예외가 나는 경로가 있다:
  OpenAI 호환 서버가 `choices: []` 를 주면 `response.choices[0]` 의 `IndexError`, SDK 의 `APIResponseValidationError` (잡는 목록에 없다),
  strict 가 아닌 응답의 형 어긋남(`TypeError`), 일관성 검수의 집계(`_outcome`)는 `try` 밖이다. 어느 것이든 유료 생성이 끝난 버전이
  `FAILED(UNEXPECTED_ERROR)` 가 되고 앞선 검수기의 결과까지 버려진다. FAILED 는 종단 상태다.
  ADR-0015·0018 은 "VLM 장애로 버전을 FAILED 로 만들지 않는다" 고 적는다.
- 제안: 세 겹으로 막는다. LLM 클라이언트는 응답 모양 접근과 SDK 의 나머지 오류를 `LLMBadResponse`·`LLMUnavailable` 로 바꾼다.
  검수기 골격(M-04)은 단위마다 예상 밖 예외도 미판정으로 남긴다 (`log.exception`). `CheckSuite` 는 마지막 방어선으로
  한 검수기의 실패를 기록하고 나머지를 계속한다.
- 담당·시점: **감사 · 지금** (테스트 먼저) · 상태: 열림

### A-14 · 낮음 · SQLite 를 여러 스레드가 쓸 때의 설정이 없다
- 어디: `src/frame/adapters/persistence/sqlalchemy/engine.py:18-33` — `PRAGMA foreign_keys=ON` 만 건다.
- 근거: 요청 스레드, 작업 스레드 2개, 진행 기록 관찰자, 주기 복구가 같은 파일 DB 에 쓴다. WAL 도 `busy_timeout` 도 없어
  쓰기가 겹치면 기본 5초 뒤 `database is locked` 가 난다. #21 의 평가 도구가 "같은 DB 를 쓰는 서버는 끄고 돌린다" 고 안내하는 이유다.
- 제안: 파일 DB 일 때 `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=30000`. 이슈 #4 의 'SQLite 한계 문서화' 와 함께.
- 담당·시점: **감사 · 지금** (`engine.py` 는 다른 브랜치가 건드리지 않았다. 테스트 먼저) · 상태: 열림

### A-15 · 낮음 · 버전 목록 조회가 버전마다 쿼리를 두 번 더 한다
- 어디: `adapters/persistence/sqlalchemy/repositories.py:180-194` (`_to_domain` 이 버전마다 장면을 조회),
  `application/use_cases/queries.py:146,177-178` (대시보드가 버전마다 검수 결과를 조회).
- 영향: 버전 N개인 프로젝트의 대시보드가 1 + 2N 번 조회한다. 시연 규모에서는 문제없다. 기록만 한다.
- 담당·시점: 기록 (MySQL 전환, 이슈 #5 때 함께) · 상태: 기록

### A-16 · 낮음 · 장면 전체를 판정하지 못한 요구 반영 결과에 `detail.predicate` 가 없다
- 어디: `src/frame/adapters/checker/vlm_requirement.py:326-337` (`detail={"method", "reason"}`) vs 정상 결과 `:266-267`.
- 근거: #21 세션이 찾았다 — `detail.predicate` 로 대상 요구조건을 찾던 평가 코드가 그 장면을 빼고 성공으로 셌다.
  화면이 `detail` 로 배지를 만든다면 같은 빈칸이 있다.
- 제안: `_undetermined` 도 `predicate`·`expected_value` 를 넣는다 (결과 형식이 한 가지가 된다).
- 담당·시점: **감사 · 지금** (VLM 검수기 정리 M-04 와 함께) · 상태: 열림

### A-17 · 중간 · 보존 검수기 둘을 함께 켜면 재사용 장면이 두 번 세어진다
- 어디: `adapters/checker/ssim_preservation.py:51`, `adapters/checker/vlm_preservation.py:147` — 둘 다 재사용 장면마다
  `score=1.0, detail.method="identical"` 결과를 낸다. `domain/metrics.py:43-44` `preservation_rate` 는 종류가 PRESERVATION 인 결과를 모두 평균한다.
- 영향: `GET /checks` 요약·비교·대시보드의 보존율이 재사용 장면 쪽으로 두 배 기운다. #21 은 평가에서 기준별로 갈라 피했다.
- 제안: 지표 1 의 측정 기준(연구 문서 §7 결정 2: `vlm_keep` / `ssim`)이 정해져야 한다. 그 전에 할 수 있는 것은
  재사용 장면 결과를 장면당 하나로 줄이는 것이다 (요약 계산에서 `method=identical` 을 장면 키로 한 번만 센다).
- 담당·시점: **결정** (결정 2) · 상태: 열림

### A-18 · 낮음 · 보존·일관성 비율의 계산 방식이 미판정 유무에 따라 바뀐다
- 어디: `src/frame/domain/metrics.py:23-29` `_rate` — 모든 결과에 점수가 있으면 점수 평균, 하나라도 없으면 PASS 비율.
- 영향: SSIM 기준에서 한 장면이 미판정이 되면 값이 '평균 0.93' 에서 'PASS 2/3' 로 뛴다. #21 ADR 의 한계에 적혀 있다.
- 제안: A-17 과 같은 결정에서 함께 정한다 (점수 평균과 PASS 비율을 따로 내는 것이 가장 단순하다).
- 담당·시점: **결정** · 상태: 열림

### A-19 · 낮음 · 사용자가 준 저장소 URI 로 저장 루트 안의 파일을 내려받을 수 있다
- 어디: `src/frame/presentation/routers/versions.py:85-91` (`get_reference_image`), `application/use_cases/create_project.py:52-60`
  (`image_uri` 를 그대로 저장).
- 근거: `reference_images[].image_uri` 에 `local://<키>` 를 주면 그 경로가 참조 이미지로 내려온다. 키는 추측하기 어려운 값이라 위험은 낮다
  (오케스트레이터가 concept-ux 검토 때 '낮은 위험' 으로 적었다).
- 제안: 업로드(#12)가 생기면 이 저장소의 스킴으로 시작하는 URI 는 업로드 결과만 받는다.
- 담당·시점: **#10** (전달 노트 6번. 그 브랜치의 ADR-0024 §2 는 직접 지정한 저장소 URI 를 그대로 두기로 적었다. 하지 않으면 감사가 머지 뒤 P-14) · 상태: 전달

### A-20 · 중간 · 수정 전파가 배경 객체를 관계의 주체로 받는다
- 어디: `src/frame/domain/services/propagation.py:141-169` (`_build_requirement` 는 상대 객체만 본다, 152-158),
  `src/frame/domain/services/revision_planning.py:46-51` (전파 결과를 검증하지 않는다).
- 근거 (재현): 배경 `cafe` 에 `holds -> cup` 을 SCENES 범위로 요청하면 `resolve_revision` 이 CHANGE 로 통과하고
  요구조건 `intro:cafe:holds:cup` 이 생긴다. 같은 IR 은 `validate()` 에서 "배경 객체는 관계 조건의 주체가 될 수 없습니다" 로 거부된다.
  API 로는 201 이고 생성까지 COMPLETED 다. ADR-0008 은 이 규칙을 "IR.validate, 수정 전파, 보정 단계 모두에서 강제" 한다고 적는다.
- 제안: `resolve_revision` 이 전파한 IR 을 돌려주기 전에 `validate()` 한다 (규칙이 한 곳에 남는다). 테스트 먼저.
- 담당·시점: **감사 · 지금** · 상태: 열림

### A-21 · 중간 · 로컬 저장소에서 외부 URL 사용자 참조가 있는 객체는 일관성 검수가 늘 미판정이다
- 어디: `src/frame/adapters/checker/vlm_consistency.py:200-211` (205행 `self._store.exists(ref.image_uri)` 가 160~166행의 `try` 안에서 불린다),
  `src/frame/adapters/storage/local_fs.py:47-50` (`local://` 가 아니면 `StorageError`).
- 근거: `FRAME_ARTIFACT_STORE=local_fs` 에서 사용자 참조가 `https://…` 이면 `exists` 가 `StorageError` 를 던지고, 그 객체는
  `STORAGE_ERROR: 지원하지 않는 URI` 로 미판정이 된다 (프레임은 이미 다 뽑은 뒤다). 메모리 저장소에서는 `exists` 가 거짓이라 정상 판정된다.
  업로드(#12)가 들어오기 전까지 사용자 참조는 외부 URL 뿐이다. 같은 사정을 `presentation/routers/versions.py:64-68` 과
  `adapters/generator/openrouter.py:1446-1448` 은 피해 간다.
- 제안: 뿌리는 `ArtifactStore.exists()` 가 구현마다 다른 것이다 (메모리는 거짓, 로컬은 예외). 포트 계약을
  "다른 스킴·저장 루트 밖의 URI 는 거짓" 으로 정하고 로컬 구현을 맞춘다 (T-05). 검수기 계약 테스트의 저장소를 두 구현으로 돌린다.
- 담당·시점: **감사 · 지금** · 상태: **해결** (`f8cf87a` — `exists()` 는 다른 곳의 주소에 거짓. 외부 주소의 사용자 참조가 있는 객체가 두 저장소 모두에서 판정되는 테스트를 더했다)

### A-22 · 중간 · 일관성 검수가 응답의 빈 곳을 통과 쪽으로 읽고, 묻지 않은 속성의 이탈을 센다
- 어디: `src/frame/adapters/checker/vlm_consistency.py:276-297` (집계), `:281-285` (`dropped`), `:247` (응답 프레임을 `frame_id` 정확 일치로 찾는다).
- 근거: (a) 응답에 없는 프레임은 `frames.get(...) or {}` → '안 보임' 으로 분모에서 빠진다. 모델이 네 프레임 중 둘만 답하면 그 둘로 PASS 1.0 이 된다.
  (b) 보이지만 `same_subject` 가 `null` 인 프레임은 속성 이탈(`kept=false`)이 있어도 분모에서 빠진다.
  (c) 그 프레임에 묻지 않은 속성(`pose` 등)의 `kept=false` 도 이탈로 센다 — 정체성 추출기는 같은 문제를 `_verdicts` 로 막는다
  (`adapters/identity/vlm_crop.py:466-484`). (d) 세 검수기 중 이것만 `match_answers` 를 쓰지 않아, 모델이 `frame_id` 에 라벨을 통째로
  돌려주면 전부 '안 보임' 이 되고 사유는 "객체가 보이는 표본 프레임이 없음" 이다 (`adapters/vlm_answers.py` 머리말이 적은 실호출 현상).
- 제안: 프레임을 짧은 id(`F1…`)로 가리키고 `match_answers` 로 맞춘다. 그 장면에 물은 속성과 이름이 맞는 이탈만 센다.
  응답에 없는 프레임 수를 `detail` 에 남기고, 판정된 프레임이 표본의 일정 비율에 못 미치면 미판정으로 한다 (비율은 §12.3 결정 4).
- 담당·시점: **감사 · 지금** (동작이 바뀌므로 테스트 먼저. M-04 뒤) · 상태: 열림

### A-23 · 중간 · 요구 반영 검수가 부모의 미판정까지 물려받는다
- 어디: `src/frame/adapters/checker/vlm_requirement.py:138-166` `_inherited` — 판정값을 가리지 않는다.
- 근거: 부모를 검수할 때 VLM 시간 초과로 미판정이 된 장면을, 그 장면을 다시 만들지 않는 모든 수정 버전이 그대로 물려받고 다시 묻지 않는다.
  ADR-0018 은 "부모에 판정이 없으면 새로 판정한다" 고 적는다.
- 제안: PASS·FAIL 만 물려받는다. 테스트 먼저.
- 담당·시점: **감사 · 지금** · 상태: 열림

### A-24 · 중간 · LLM 호출의 재시도·시간·출력 길이에 명시한 상한이 없다
- 어디: `src/frame/adapters/llm/openai_client.py:101` (`openai.OpenAI(api_key=…, base_url=…, timeout=…)` — 재시도 횟수를 주지 않는다),
  `adapters/checker/vlm_requirement.py:118` 등 (`timeout_s = 180.0` 이 생성자 기본값), `config/registry.py:255-288` (검수기에 시간 제한을 넘기지 않는다).
- 근거: 설치된 SDK 의 기본 재시도는 2회다 (`openai.DEFAULT_MAX_RETRIES == 2` 확인). VLM 이 응답하지 않으면 호출 하나가 180초 × 3 ≈ 9분이고,
  첫 실패 뒤에도 장면·객체마다 계속 부른다. `FRAME_LLM_TIMEOUT_S` 는 구조화·해석기에만 닿는다. 요청에 출력 토큰 상한이 없다.
  리스(A-06)와 시연 런처의 기동 대기(O-05)가 이 시간에 걸린다.
- 제안: `max_retries` 를 명시한다 (0 또는 1). 검수 실행 한 번 안에서 시간 초과·연결 실패가 나면 남은 단위는 부르지 않고 미판정으로 둔다.
  VLM 시간 제한과 출력 상한의 설정 연결은 A-03 과 함께.
- 담당·시점: **감사 · 지금** (`openai_client.py`·검수기) / 머지 뒤 (설정) · 상태: 열림

### A-25 · 중간 · LLM 키 파일 파서가 변수 이름과 공급자를 가리지 않는다
- 어디: `src/frame/adapters/llm/keys.py:8,20-27`, 같은 규칙의 `src/frame/tools/demo.py:100-103`.
- 근거 (가짜 값으로 재현): 파일에 `OPENROUTER_API_KEY=sk-or-…` 줄이 `OPENAI_API_KEY=sk-proj-…` 보다 앞에 있으면 앞의 키를 돌려준다.
  그 키가 OpenAI(또는 `FRAME_LLM_BASE_URL`)로 간다. `sk-` 토큰이 없으면 첫 `이름=값` 줄의 값을 키로 쓴다 (`.env` 를 키 파일로 주면
  다른 비밀이 나간다). 반대편 `adapters/generator/openrouter.py:216-241` 은 `sk-or-` 만 받아 이 실수를 막는다.
- 제안: 이름이 있는 줄은 `OPENAI_API_KEY` 만 읽는다. 이름 없는 키는 한 줄짜리 파일일 때만. 주석 줄은 뺀다. OpenAI 주소로 `sk-or-` 는 거절한다.
- 담당·시점: **감사 · 지금** · 상태: 열림

### A-31 · 중간 · 뼈대 어댑터가 기동이 아니라 실행 중에 실패한다
- 어디: `src/frame/adapters/tracker/opencv_stub.py:21-25` (`track` 이 늘 `TrackingError`), `adapters/generator/stubs.py:47-52`
  (`generate_scene` 이 늘 `GenerationError`), 레지스트리 등록 `config/registry.py:184,248`.
- 근거: README 의 설정 표는 `FRAME_TRACKER` 값을 `fake · opencv` 로 안내한다 (`README.md:64`). `opencv` 로 두고 실제 생성기를 쓰면
  유료 생성이 끝난 뒤 추적 단계에서 `FAILED(TRACKING_ERROR)` 가 된다. 다른 어댑터는 "키나 패키지가 없으면 기동 시점에 실패" 가 원칙이다
  (`config/settings.py:78`).
- 제안: 뼈대 어댑터는 조립 때 `ConfigurationError` 로 거절한다 (서버가 뜨지 않는다). 문서 표에는 '(뼈대)' 를 적는다 (D-03).
- 담당·시점: **감사 · 지금** (어댑터) / 머지 뒤 (문서) · 상태: 열림

### 그 밖의 어댑터 발견 (낮음)

| ID | 무엇 | 어디 | 제안 | 시점 |
|---|---|---|---|---|
| A-26 | 구조화 입력의 가장자리. (a) strict 가 아닌 경로에서 형이 어긋난 응답(`"expected_value": 3`)이 `AttributeError` 로 재시도 고리를 빠져나간다. (b) 보정이 빈 문자열 키를 `null` 로 고치지 않아 재시도 3회를 다 쓴다. (c) DSL 이 값의 `#` 을 주석으로 잘라 `cup.color = #ff0000` 이 오류다 (재현. 어휘는 COLOR 를 '색 이름/HEX' 로 적는다). (d) 값의 `@` 는 장면 목록으로 읽힌다 | `structurer/llm_structurer.py:56`, `repair.py:105-126`, `rule_based.py:86` | 예외 목록에 `AttributeError`, 보정 규칙 한 줄, 주석은 줄 머리나 공백 뒤의 `#` 만 | (a)(c)(d) 지금. (b) 보정 규칙은 #17 머지 뒤 — 골드셋 리플레이 점수가 보정 결과에 기댄다 (P-16) |
| A-27 | 정체성 추출. 가는 박스 하나가 그 클립의 추출 전체를 실패시킨다 (이미 뽑은 다른 객체의 기준도 돌려주지 않는다). 술어만 온 속성 이름(`color`)을 물은 이름(`color: teal`)과 맞추지 못해 거절이 무시된다. 같은 클립·객체를 두 작업이 동시에 뽑으면 서로 다른 기준이 나온다 (추정) | `identity/vlm_crop.py:217-226,250-255,475-483,187-200` | 객체별로 감싸 '못 찾음' 으로, 이름 맞춤 보강, 키별 잠금 | 지금 (M-04 와 함께) |
| A-28 | 유지 요소 검수가 프레임 시각을 부모 클립 길이로만 계산한다. 자식이 더 짧으면 프레임을 못 뽑아 미판정 | `checker/vlm_preservation.py:169,226-228` | 두 길이 중 짧은 쪽 | 지금 |
| A-29 | 밖으로 나가는 글. ffmpeg 오류 꼬리에 서버 임시 경로(`C:\Users\…\Temp\frame-frames-…`)가 들어가 `detail.reason` 으로 API 에 나간다. 구조화 프롬프트에 참조 이미지 URI 원문이 들어간다 (서명 URL 이면 LLM 공급자에게 간다). 키 표시가 9자 키에서 7자를 드러낸다 (`/health` 로 나간다) | `adapters/video_tools.py:111,123`, `structurer/llm_prompt.py:121-124`, `llm/keys.py:31-32` | 사용자용 사유와 로그용 원문 분리, 프롬프트에는 번호와 객체 키만, `sk-` 로 시작할 때만 접두사 표시 | 지금. 프롬프트는 #17 머지 뒤 — 프롬프트가 바뀌면 그 브랜치의 기록과 어긋난다 (P-16) |
| A-30 | 변경 요청 해석기 두 구현의 경계가 다르다. 없는 객체: 규칙 기반은 그대로 돌려줘 404, LLM 은 422. LLM 이 바꾸는 것 자체를 유지 요소에 넣으면 계획 전체가 422. LLM 이 키에 줄을 붙여 돌려주면(`cup (prop)`) "객체가 없습니다" | `change_request/llm.py:170-208`, `rule_based.py:53,80-91` | 충돌하는 유지 요소는 버리고 notes 에 적는다. 키 맞춤은 `requested_key` 재사용. 두 구현이 같은 오류 코드 | 지금 |

### 영상 생성 어댑터 (유료 경로)

먼저 결론: 일상 경로에서 반드시 재과금되거나 키·서명 URL 이 새는 경로는 찾지 못했다. 접수 뒤의 오류는 모두 재시도 불가로 올라가고
(raise 지점 15곳 확인), 접수 기록은 폴링 전에 남고, 오류 문구는 키·주소를 가린 뒤 나간다. 아래는 조건이 맞을 때의 경로다.

### A-32 · 중간 · 클립이 남아 있는 완료 기록에도 24시간 기한을 적용한다
- 어디: `src/frame/adapters/generator/openrouter.py:1328-1339` `_resumable` — 상태를 가리지 않고 `submitted_at` 에서 24시간이 지나면 없는 것으로 본다.
- 근거: ADR-0013 은 "클립까지 저장한 결과는 생성 캐시가 기한 없이 맡는다" 를 전제로 기한을 정했다. ADR-0016·0021 이 만든 '원장에만 있는
  완료 결과'(임시 결과, 편집 거절 뒤 조건 키로 남긴 사본, 시간 제한으로 버려진 뒤 늦게 끝난 결과)에는 그 전제가 없다.
  예: 편집 모드 `video_reference` 에서 부모 주소가 아직 없어 새로 생성한 결과는 캐시에 없고 조건 키 원장에만 `completed` 로 남는다.
  25시간 뒤 같은 수정을 다시 실행하면 클립이 저장소에 그대로 있는데도 새로 제출한다 (가짜 전송으로 확인: 제출 1 → 2).
- 제안: 기한은 제공자에게 다시 물어야 하는 기록(`submitted`, 또는 `completed` 인데 클립이 없을 때)에만 적용한다.
- 담당·시점: **감사 · 지금** (테스트 먼저) · 상태: **해결** (`7265865` — 기한은 제공자에게 다시 물어야 하는 항목(결과 미회수, 또는 클립이 저장소에 없는 완료 항목)에만 적용한다.
  클립이 남아 있는 완료 항목은 기한 없이 그 클립을 돌려준다. ADR-0013 에 보완 줄)

### A-33 · 중간 · REPAIR 가 고치려는 요구조건이 결과 키에 없다
- 어디: `src/frame/application/ports/generator.py:106-127` `generation_fingerprint` — 수정 지시 가운데 유지 요소(`keep`)만 넣는다.
- 근거: 같은 부모의 같은 장면에서 요구조건 A 를 REPAIR 한 뒤 요구조건 B 를 REPAIR 하면, 두 요청의 조건과 seed(둘 다 부모 seed + 1,
  `domain/revision.py:182-184`)가 같아 키가 같다. 둘째 요청은 캐시·원장에서 첫째의 클립을 그대로 받는다. B 를 지키라는 지시
  (`Must show:` 줄, `openrouter.py:377-396,483-491`)는 모델에 간 적이 없다. docstring 의 "꼭 지켜야 할 요구조건은 이미 조건(또는 seed)에
  들어 있다" 는 REPAIR 에는 맞지 않는다 — seed 는 무엇을 고치는지 담지 않는다.
- 제안: REPAIR 일 때만 `enforce` 의 (키, 값)을 키에 넣는다. CHANGE 와 지시 없는 요청의 키는 그대로다 (기존 캐시·원장과 호환).
- 담당·시점: **감사 · 지금** (테스트 먼저) · 상태: **해결** (`63428d0` — REPAIR 일 때 `enforce` 의 (키, 값, 관계 대상)을 `generation_fingerprint` 에 넣는다. CHANGE 와 지시 없는 요청의 키는
  그대로다. 이 변경 전의 REPAIR 결과는 새 키로 찾지 못해 한 번 다시 만들어진다. ADR-0017 에 보완 줄)

### A-34 · 중간 · 시간 제한으로 버려진 작업 스레드가 제출 전이면 마감 뒤에 제출한다
- 어디: `src/frame/adapters/generator/decorators.py:139-153` (`pause_before_retry` 는 재시도 데코레이터의 시도 사이에서만 불린다),
  `openrouter.py:647` (키 잠금 대기), `:1483-1493` (429·502 뒤 대기했다가 다시 POST), `:925-966` (부모 주소 대기).
- 근거: 제출이 429 → 어댑터가 Retry-After 만큼 대기 → 그 사이 범용 마감이 지나 버전은 FAILED → 버려진 스레드가 다시 POST 해 접수된다
  (가짜 전송으로 확인). 결과는 원장에 남아 같은 조건의 다음 요청이 이어 받지만, 사용자가 조건을 바꾸면 낭비다.
  ADR-0012 는 폴링 중인 스레드와 재시도 사이만 다뤘다.
- 제안: '버려졌는가' 확인을 작은 모듈로 빼고, 어댑터가 잠금을 얻은 직후·매 POST 직전·부모 주소 대기 루프에서 부른다.
- 담당·시점: **감사 · 지금** (테스트 먼저) · 상태: **해결** (`d7a19a1` — '버려졌는가' 신호를 `adapters/generator/abandonment.py` 로 빼고 OpenRouter 어댑터가 매 POST 직전에 확인한다.
  잠금을 얻은 직후와 부모 주소 대기 루프에는 따로 넣지 않았다: 그 뒤의 제출이 같은 확인에 걸리고, 이미 접수된 작업을 이어 받는 길은
  끝까지 가야 결과가 원장에 남아 다음 요청이 다시 내지 않는다. ADR-0012 에 보완 줄)

### A-35 · 중간 · 편집이 거절된 뒤, 같은 조건의 작업이 진행 중이어도 새로 제출한다
- 어디: `src/frame/adapters/generator/openrouter.py:746-749` — 조건 키 기록이 `completed` 가 아니면 없는 것으로 본다.
- 근거: 같은 조건의 작업이 조건 키로 접수돼 진행 중이거나 시간 초과로 `submitted` 로 남아 있을 때, 편집을 켠 요청의 편집 제출이
  거절되면 둘째 작업을 제출한다 (둘 다 과금). `tests/unit/generator/test_openrouter_edit.py:493-515` 가 이 순서의 제출 3번을 기대값으로 고정한다.
  그 테스트의 목적은 "앞선 작업의 원장 항목을 덮어쓰지 않는다" 이고, 진행 중인 경우의 의도는 ADR-0016 에 없다.
- 제안: 조건 키 기록이 `submitted` 면 이어 받는다 (조건 키 잠금 → `_collect(resumed=True)`). 의도된 절충이라면 ADR-0016 에 적는다.
- 담당·시점: **결정** (의도 확인) → 감사 · 상태: 열림

### A-36 · 중간 · 편집 모델의 제출 전 확인이 실패하면 새로 생성으로 넘어가지 않는다
- 어디: `src/frame/adapters/generator/openrouter.py:699-702` (`_edit_body` 가 `try` 밖), `:1122-1124`, 미리보기 `:1054-1058`.
- 근거: `FRAME_VIDEO_EDIT_MODEL` 이 모델 목록에 없거나 장면이 편집 모델의 최대 길이보다 길면, 미리보기는 `EDIT` 로 안내하고
  실행은 제출 없이 `GenerationError` 로 끝나 수정 버전이 FAILED 가 된다 (가짜 전송으로 확인). 메시지는 "FRAME_VIDEO_MODEL 을 확인하세요" 다.
  제출 단계의 400·404 는 새로 생성으로 넘어간다 (`:128`). ADR-0016 은 "편집 모델이 요청을 받지 않아도 수정은 실패하지 않는다" 고 적는다.
- 제안: `_edit_body` 호출을 감싸 실패하면 거절 사유로 바꿔 새로 생성한다 (과금 전이다). 미리보기도 같은 경우 `GENERATE` + 사유.
- 담당·시점: **감사 · 지금** (테스트 먼저) · 상태: **해결** (`7d4a140` 구조 변경, `0eb1214` 수정 — 제출 전 확인이 재시도할 수 없는 오류(목록에 없는 편집 모델, 장면이 최대 길이보다 김)로 막히면
  사유를 남기고 새로 생성한다. 미리보기는 `GENERATE` 와 사유. 목록에 없는 모델의 안내가 그 모델의 설정 이름(`FRAME_VIDEO_EDIT_MODEL`)을
  가리킨다. 모델 목록을 받지 못한 일시 오류는 편집을 포기하지 않는다. ADR-0016 에 보완 줄)

### A-37 · 중간 · 폴링·다운로드의 일시 오류 분류가 세 곳에서 다르다
- 어디: `src/frame/adapters/generator/openrouter.py:119` (`_TRANSIENT_GET` = 408·409·425·429·500·502·503·504), 쓰는 곳 `:1528-1534`·`:1568-1571`,
  부모 주소 조회는 `:982` (`>= 500`).
- 근거: 상태 조회가 520·524 를 한 번 돌려주면 대기가 바로 끝나 버전이 FAILED 가 된다 (503 은 넘긴다). 작업은 원장에 남아 재과금은 없다.
  ADR-0011 은 "5xx" 라고 적었다.
- 제안: `status in {408, 409, 425, 429} or status >= 500` 한 함수를 세 곳이 쓴다.
- 담당·시점: **감사 · 지금** · 상태: **해결** (`151de2b` — `_may_pass`: 408·409·425·429 와 모든 5xx. 상태 조회·결과 내려받기·부모 작업 조회가 같이 쓴다)

### A-41 · 중간 · 스레드 디스패처와 SQLAlchemy 기본 주소의 조합에서 버전이 멈춘다
- 어디: `src/frame/adapters/persistence/sqlalchemy/engine.py:20-25` (in-memory SQLite 는 `StaticPool` — 연결 하나를 모든 스레드가 쓴다),
  `docker/Dockerfile:8-12` (`FRAME_PERSISTENCE=sqlalchemy`·`FRAME_JOBS=thread` 만 정하고 DB 주소는 기본값이다).
- 근거 (재현): `jobs="thread"` 로 프로젝트 생성 → 생성 접수를 하면서 다른 스레드가 화면처럼 `GET /versions/{id}` 를 보내게 하면,
  memory 저장소 8/8 완료, SQLite 파일 8/8 완료, **SQLAlchemy + in-memory SQLite 3/8** (나머지는 DRAFT·QUEUED·PROCESSING 에 멈춤).
  워커의 예외는 로그에만 남아 화면에는 처리 중으로 보인다. compose 없이 이미지만 띄우면 이 조합이다.
- 제안: in-memory 주소일 때 스레드마다 연결을 갖는 공유 캐시 방식으로 엔진을 만든다 (연결 하나는 열어 둔다). 조합 테스트를 둔다 (T-02).
- 담당·시점: **감사 · 지금** (테스트 먼저) · 상태: 열림

### 그 밖의 생성기 발견 (낮음)

| ID | 무엇 | 어디 | 제안 | 시점 |
|---|---|---|---|---|
| A-38 | 밖으로 나가는 값과 전송 계층. (a) 키 값 가운데에 줄바꿈이 끼면 http.client 의 `ValueError("Invalid header value b'Bearer sk-or-…'")` 가 전송 계층을 그대로 지나 `error_message` 에 키 전체가 들어간다 (확인. 키는 양끝 공백만 떼므로 닿기 어렵다). (b) ffmpeg 실패 꼬리의 임시 경로가 `error_message` 로 나간다. (c) `_scrub` 이 `https://user:pass@host` 의 userinfo 와 스킴 없는 서명 조각을 못 지운다. (d) 전송 계층: 응답 크기·총 시간 상한 없음, 오류 본문 읽기의 `IncompleteRead` 누락, 스킴 제한 없음 (지금은 호출 주소가 모두 검증된 `base_url` 에서 나온다). (e) HTTPS URI 를 주는 원격 저장소를 붙이면 부모 클립의 서명 주소가 원장·메타데이터에 남는다 | `adapters/http_transport.py:69-87`, `application/pipeline/runner.py:45-48`, `openrouter.py:117,1645-1651,938-939,1146` | 키 형식 검사(인쇄 가능한 ASCII), `ValueError` → 키 없는 `TransportError`, 임시 폴더는 `<tmp>` 로, http(s) 만 허용, 상한을 둔 읽기, 기록에는 호스트와 경로 해시만 | 지금 |
| A-39 | 상태와 설정의 가장자리. 상태 조회의 200 + HTML 한 번은 즉시 실패하고 (연결 오류·503 은 넘긴다), 모르는 상태 문자열(`queued`, `canceled`)은 600초 시간 초과까지 폴링한다. `timeout_seconds=nan` 이 검사를 지나 무한 폴링이 된다. `GenerationTimeout` 의 `retryable` 기본값이 참이라 새 raise 지점이 빠뜨리면 다시 제출한다. 환경 변수의 키는 `sk-or-` 검사를 하지 않는다 (파일만 한다). 작업 id 형식이 바뀌면 편집이 기록 없이 꺼진다. 재시도 데코레이터의 백오프를 넘기는 곳이 없어 0초다 | `openrouter.py:1535-1545,594-595,225-230,129,892-903`, `application/errors.py:20-26`, `config/container.py:173-175` | 형식이 깨진 응답은 몇 번까지 일시 오류로, 모르는 상태는 처음 볼 때 경고, `math.isfinite` 검사, 기본값 False | 지금 |
| A-40 | 기록과 통계. 원장 기록에 실패한 뒤에도 시간 초과 문구는 "이어 받는다" 고 안내한다. 정규화 실패 때 원본 보관 실패가 로그 없이 사라진다. 재사용 판정이 파일이 있는지만 본다 (0바이트·잘린 클립도 재사용). 이어 받은 작업의 `generation_seconds` 는 남은 대기 시간이라 계획의 어림 시간이 흐려진다. 편집 대체 사유가 실제 원인과 다르게 적힐 수 있다 (조회 실패도 "아직 나오지 않음"). 조립기의 스트림 복사 판정은 코덱·크기·표시 fps 만 본다. 쓰이지 않거나 두 벌인 것: `stubs.compile_prompt` (뼈대 어댑터만 부르고 결과를 버린다), `RetryingVideoGenerator` 의 `backoff_seconds` (값을 넘기는 곳이 없다), `OPENROUTER_API_BASE`·`DEFAULT_VIDEO_MODEL` (설정 기본값과 두 벌) | `openrouter.py:845,1508-1513,1589-1598,651-656,1186-1189,979-998`, `ffmpeg.py:242-248`, `stubs.py` | 기록 실패를 들고 다니다 문구를 바꾼다, 안쪽 `except` 에 로그, 기록에 바이트 수, 사유를 열거값으로 | 지금 (M-01 과 함께) |

## 4. 계층 규칙

AST 로 `src/frame` 의 모든 import 를 계층으로 묶어 보았다 (함수 안 import, `TYPE_CHECKING` 포함).

| 간선 | 수 | 판정 |
|---|---|---|
| application → domain | 84 | 허용 |
| adapters → domain / application | 74 / 74 | 허용 (`application` 쪽은 `ports` 53, `errors` 21) |
| presentation → application / domain / config | 13 / 16 / 9 | 허용 |
| config → adapters / application / domain | 23 / 12 / 3 | 허용 (조립 지점) |
| evaluation → domain | 3 | 허용 |
| tools → application / config / presentation | 2 / 2 / 1 | 허용 |
| presentation → 패키지 최상위 `frame` | 1 | 허용 (`from frame import __version__`, `presentation/app.py:20`. 최상위에는 버전 문자열뿐이다) |
| domain → 다른 계층·외부 패키지 | 0 | — |
| 외부 패키지 | fastapi·starlette·pydantic 은 presentation, sqlalchemy·openai·imageio_ffmpeg 는 adapters 에만 | 규칙대로 |

### L-01 · 중간 · 계층 규칙이 문서에만 있다
- 어디: `docs/ARCHITECTURE.md:79-87` ("의존 규칙 (import 로 강제)") — 강제하는 검사가 없다. 지금 위반이 0건인 것은 작성자들이 지켰기 때문이다.
- 제안: pytest 아키텍처 테스트로 CI 에 건다 (새 도구 설치 없음. §9 G2).
- 담당·시점: **감사 · 2단계** · 상태: **해결** (`681501d` — `tests/architecture/test_layer_rules.py`)

### L-02 · 낮음 · 문서의 규칙 표가 실제 허용 범위와 다르다
- 어디: `docs/ARCHITECTURE.md:81-87`.
- 근거: 표는 어댑터가 `application.ports`·`domain` 만 import 한다고 하지만 21개 어댑터 모듈이 `frame.application.errors` 를 쓴다
  (어댑터 오류 계층이 거기 있다). 표는 presentation 이 `config.Container` 타입만 쓴다고 하지만 `presentation/mappers.py:22`·
  `routers/settings.py:18`·`app.py:22-23` 이 `config.settings.Settings` 와 `config.container.change_interpreter_key` 를 쓴다.
  `evaluation`·`tools` 행은 없다.
- 제안: 표를 실제 규칙으로 고친다 — adapters: `application.ports`·`application.errors`·`domain`; presentation: `application`·`domain`·`config`;
  evaluation: `domain`·`application`; tools: 전부. `mappers.py` 는 `Settings` 대신 `bool` 하나를 받게 바꿀 수 있다 (머지 뒤 M-03 과 함께).
- 담당·시점: **감사** · 표는 ADR-0030 에 먼저 적고, `ARCHITECTURE.md` 는 머지 뒤 (#21 브랜치가 고치고 있다. P-11) · 상태: 열림

### L-03 · 낮음 · 어댑터 공용 모듈이 `adapters/` 최상위에 흩어져 있다
- 어디: `src/frame/adapters/common.py`, `http_transport.py`, `video_tools.py`, `vlm_answers.py`.
- 근거: 문서의 규칙은 "다른 어댑터를 import 하지 않는다 (공용 common 제외)" 다. 실제로는 `checker → video_tools`(4)·`vlm_answers`(2),
  `identity → video_tools`(2)·`vlm_answers`(1), `generator → http_transport`(1) 이다. 어느 것이 공용인지는 파일 이름으로만 알 수 있다.
- 제안: `adapters/shared/` 패키지로 모으고 옛 경로는 다시 내보낸다. 규칙은 "어댑터 패키지끼리는 `adapters.shared` 만 import" 가 된다.
- 담당·시점: **감사 · 지금** (R-03 과 함께) · 상태: 열림

### L-04 · 낮음 · `evaluation` 이 `application` 을 쓰기 시작했다
- 근거: #21 `evaluation/edit_runner.py:19-22`, #17 `evaluation/structuring_gold.py:20-21`. 둘 다 유스케이스·포트만 쓰고 어댑터·컨테이너는
  쓰지 않는다. 규칙을 정해 두면 된다 (L-02 의 표).
- 담당·시점: **감사 · 2단계** (아키텍처 테스트에 반영) · 상태: **해결** (`681501d` — `evaluation` 은 `domain`·`application` 만)

## 5. 모듈 구조와 개념 경계

기준: 모듈은 **개념 단위**로 나눈다. 뒤에 들어오는 사람이 "이 일은 어디에 있는가" 를 패키지 이름만 보고 찾고, 패키지 하나를 읽을 때
머리에 담아야 할 개념이 하나이게 한다. 디렉터리가 깊어지는 것은 괜찮다. 이 기준으로 본 것이 5.1 이고, 큰 모듈별 발견이 5.2 다.

### 5.1 개념 경계와 인지 부담

기준 (사용자 지시): 리팩터링은 개념 단위 추상화를 기준으로 모듈을 나눠, 뒤에 들어오는 사람의 인지 부담을 줄인다.
파일 크기나 기술 종류(스키마·매퍼·열거형·데코레이터)가 아니라 개념으로 나눈다. 이 분류의 발견은 세 가지로 갈랐다.

- **섞임** — 한 모듈·패키지에 여러 개념이 들어 있다. 기술 종류로 묶은 모듈도 여기에 든다.
- **흩어짐** — 한 개념이 같은 계층 안의 여러 곳에 나뉘어 있거나 같은 코드가 여러 벌이다. 포트 → 유스케이스 → 어댑터로 계층을 건너는 것은
  이 구조에서 당연하므로 세지 않는다. 그 길은 모듈 지도가 잇는다.
- **이름** — 이름이 하는 일을 말하지 않거나, 같은 낱말이 서로 다른 개념을 가리킨다.

| 종류 | 이 절의 발견 | 다른 절의 발견 가운데 여기에 드는 것 |
|---|---|---|
| 섞임 | K-04, K-05 | M-01, M-02, M-03, M-05, M-06 |
| 흩어짐 | K-01 | M-04, M-07, R-01, R-03, R-04, R-07, R-08, R-09, R-10, C-09 |
| 이름 | K-02, K-05 | D-08 (일부) |
| 길잡이가 없음 | K-03 | D-04 (낡은 모듈 지도) |

#### 개념 지도 — 지금 있는 곳

개념마다 계층을 따라 어디에 있는지와 걸리는 것을 적었다. 3단계에서 이 표를 목표 배치에 맞춰 고쳐 `docs/MODULE_MAP.md` 로 둔다 (K-03).

| 개념 | 도메인 (`domain/`) | 포트 · 유스케이스 · 파이프라인 (`application/`) | 어댑터 · 그 밖 | 걸리는 것 |
|---|---|---|---|---|
| 구조화 (요구사항 → IR) | `ir.py`, `vocabulary.py`, `identifiers.py` | `ports/structurer.py` · `use_cases/create_project.py`, `structure_requirements.py` | `adapters/structurer/` | 이름: `repair.py` 는 '보정' 이고 REPAIR(다시 맞추기)와 다른 뜻 (K-02) |
| 생성 (장면 클립 만들기) | `artifacts.py` | `ports/generator.py` (`VideoGenerator`·`SceneGenerationRequest`) · `pipeline/stages.py` `GenerateStage`, `pipeline/requests.py` | `adapters/generator/` | 섞임: 패키지에 네 개념이 있고 포트 모듈 하나에 포트 둘과 부모 클립·미리보기 타입이 있다 (K-04), 단계 하나가 세 가지 일 (M-06) |
| 제공자 OpenRouter | — | — | `generator/openrouter.py`, `http_transport.py` | 섞임: 클래스 하나에 제출·대기·원장·부모 클립 편집·미리보기·프롬프트·모델 정보 (M-01) |
| 부모 클립 편집 | — | `ports/generator.py` (`ParentClip`·`RevisionDirective`·`result_key`), `pipeline/requests.py` | `generator/openrouter.py:171-197, 859-1027, 1114-1168` | 섞임: 새로 생성하는 흐름과 한 클래스에 있다 (M-01) |
| 최종 영상 조립 | `artifacts.py` | `ports/generator.py` (`VideoAssembler`) | `generator/ffmpeg.py` (`FfmpegVideoAssembler`), `generator/fake.py` | 섞임·이름: 생성기 포트 모듈·생성기 패키지 안에 있는 다른 포트 (K-04) |
| 영상 처리 (ffmpeg) | — | — | `generator/ffmpeg.py` (찾기·정보 읽기·정규화), `video_tools.py` (프레임·크기·SSIM) | 흩어짐: 실행기·로그 꼬리·정보 읽기가 두 벌 (R-03). 이름 (K-05) |
| 정체성 기준 이미지 | `services/identity.py`, `models.py` (`ReferenceImage`) | `ports/identity.py` · `pipeline/stages.py` 의 `_derive*` | `adapters/identity/` | 섞임: 생성 단계 안의 80줄 (M-06). 이름: '참조' 가 네 가지 (K-02) |
| 검수 (요구 반영 · 유지 요소 · 일관성 · 보존) | `checks.py`, `metrics.py`, `services/comparison.py` | `ports/checker.py`, `checks.py` (`CheckSuite`) · `pipeline/stages.py` `CheckStage` | `adapters/checker/` | 흩어짐: 같은 흐름이 네 벌 (M-04), 프레임 표본 (R-10). 이름: `PRESERVATION` 하나에 '보존' 과 '유지 요소' (K-02, A-17) |
| 수정 의도 · 변경 계획 | `revision.py`, `services/propagation.py`, `services/revision_planning.py` | `ports/change_request.py` · `use_cases/create_revision.py`, `plan_revision.py` | `adapters/change_request/` | 흩어짐: 도메인 세 모듈이 서로를 필요로 한다 (M-07). 이름: `revision_planning` 은 변경 계획이 아니다 (K-02) |
| 재사용 판단 (해시·키) | `ir.py`, `hashing.py`, `services/identity.py` | `ports/generator.py` | `generator/cache.py`, `generator/ledger.py`, `identity/keys.py` | 흩어짐·이름: 일곱 가지가 비슷한 이름 (K-01, R-01) |
| LLM·VLM 제공자와 API 키 | — | `ports/llm.py` | `adapters/llm/`, `generator/openrouter.py:215-246`, `tools/demo.py:45,100-103`, `presentation/routers/settings.py:29` | 흩어짐: 키 찾기·형식·가리기가 다섯 군데, `mask_key` 가 두 벌이고 결과가 다르다 (A-25, A-29, M-05) |
| 저장 (산출물 · DB) | `artifacts.py` | `ports/artifact_store.py`, `ports/repositories.py`, `ports/unit_of_work.py` | `adapters/storage/`, `adapters/persistence/` | 구현 사이의 계약 차이 (A-21, T-05) |
| 작업 실행 · 복구 | `state_machine.py`, `progress.py` | `ports/jobs.py` · `use_cases/accept_generation.py`, `run_generation_pipeline.py`, `recover_pending_versions.py` · `pipeline/` | `adapters/jobs/`, `presentation/recovery.py` | 구조는 맞다 (T-02, A-06 은 동작) |
| 추적 | `tracking.py` | `ports/tracker.py` · `pipeline/stages.py` `TrackStage` | `adapters/tracker/` | 뼈대 구현 (A-31) |
| HTTP API | — | `dto.py`, `use_cases/queries.py` | `presentation/` | 섞임: 기술 종류로 묶은 `schemas.py`·`mappers.py`·`dto.py` (M-03, K-05) |
| 조립 (설정 → 어댑터) | — | — | `config/` | 섞임: 등록 함수 하나에 포트 10종 (M-02) — 해결 `5a98ae9` |
| 평가 | `metrics.py` | — | `evaluation/` | 작업 중 브랜치 둘이 같은 층에 모듈을 더한다 (P-06) |
| 실행 도구 | — | — | `tools/` | 섞임: 런처 한 파일에 일곱 가지 (M-05) |

#### K-01 · 중간 · [흩어짐·이름] 해시·키 개념 일곱 가지가 비슷한 이름으로 흩어져 있다
- 어디: `IR.fingerprint()` → `frozen_fingerprint` (`domain/ir.py:280`), `IR.scene_fingerprint()` (`:284`),
  `SceneGenerationRequest.fingerprint` → 클립의 `fingerprint`·`source_fingerprint` (`application/ports/generator.py:82`),
  `generation_fingerprint` (`:107`), `VideoGenerator.result_key()` (`:169`), `generation_key(fingerprint, profile)` (`adapters/generator/cache.py:34`),
  `entity_appearance_fingerprint()` (`domain/services/identity.py:60`).
- 근거: 선택적 재생성·캐시·원장·정체성 기준이 모두 이 값들로 판단한다. 무엇이 어느 값에 들어가는지는 일곱 군데의 docstring 과 ADR 다섯 개
  (0003·0013·0015·0016·0017)에 나뉘어 있다. A-33(REPAIR 의 요구조건이 결과 키에 없다)은 이 지도가 없어서 생긴 종류의 결함이다.
  `generation_key` 의 인자 이름은 `fingerprint` 인데 실제로 넘기는 값은 `result_key` 다.
- 제안: 표 하나로 정리한다 — 이름, 무엇을 담는가, 누가 판단에 쓰는가 (버전 사이 재사용 / 생성 캐시·원장 / 정체성 기준의 유효성).
  그 표를 용어집과 `ports/generator.py` 의 머리말에 두고, 이름을 맞춘다 (`generation_key(result_key, profile)`). R-01 과 함께.
- 담당·시점: **감사** — 표·용어집은 지금, 이름 변경은 머지 뒤 (P-08) · 상태: 열림

#### K-02 · 중간 · [이름] 같은 낱말이 여러 개념을 가리킨다
- '수정' 계열: `revision`(수정 버전), `change`(CHANGE — 값을 바꾼다), `repair`(REPAIR — 다시 맞추기. 구조화의 `repair.py` 는 '보정' 이라는 다른 뜻이다),
  `edit` 은 세 가지다 — 부모 클립 편집(`video_edit_mode`, `compile_edit_prompt`), 수정 실험 평가(`evaluation/edit_metrics.py`),
  그리고 #10 브랜치의 `EditDraftIr`(IR 고치기).
- '계획' 계열: 변경 계획(ADR-0019, 무엇을 다시 만들지의 미리보기)은 `application/use_cases/plan_revision.py` 다.
  `domain/services/revision_planning.py` 는 이름과 달리 변경 계획이 아니라 수정 요청을 CHANGE·REPAIR 로 풀어 수정 버전 초안을 만드는 규칙이다.
- '참조' 계열: 사용자 참조 이미지(`ReferenceOrigin.USER`), 정체성 기준 이미지(`DERIVED`, 코드에서는 `identity_references`),
  제공자에 보내는 `input_references`, 편집 모드의 영상 참조(`video_reference`).
- '검수' 계열: `Checker`·`CheckSuite`·`CheckOutcome`(어댑터가 돌려주는 값)·`CheckResult`(저장되는 값)·`Verdict`. 모듈 이름 `checks.py` 가
  `domain/`(검수 결과)과 `application/`(`CheckSuite`)에 하나씩 있다.
- '보존' 계열: 검수 종류 `PRESERVATION` 하나에 두 검수기가 든다 — 화면 전체를 견주는 보존 검수(`ssim_preservation`)와 요소 단위의
  유지 요소 검수(`vlm_preservation`, 사용자가 고르는 것은 '유지할 요소' `KeepItem`). 둘을 함께 켜면 재사용 장면이 두 번 세어지는 것(A-17)이 이 겹침에서 나온다.
  문서는 '유지 요소'(55곳)와 '유지할 요소'(17곳)를 함께 쓴다.
- 제안: 용어집 `docs/GLOSSARY.md` — 낱말 하나에 뜻 하나, 한국어 이름과 코드 이름을 짝짓는다. 낱말은 연구 문서·ADR 의 것을 따른다
  (정체성 기준 이미지, 다시 맞추기(REPAIR), 유지할 요소, 변경 계획, 부모 클립 편집, 임시 결과). 새 이름은 용어집의 낱말로 짓는다.
  기존 이름은 옮기는 김에만 맞춘다 (이름만 바꾸는 커밋은 다른 브랜치와 충돌한다) — `revision_planning` 은 M-07 에서 옮길 때 `resolution` 으로.
- 담당·시점: **감사 · 지금** (용어집. ADR-0031 과 함께) · 상태: 열림

#### K-03 · 중간 · [길잡이] 패키지의 입구와 모듈 지도가 없다
- 어디: `__init__.py` 에 설명이 없는 패키지 10개 — `adapters/{change_request,checker,generator,identity,jobs,persistence/sqlalchemy,storage,structurer,tracker}`,
  `presentation/routers`. `__all__` 이 없는 패키지 8개 — `adapters`, `adapters/persistence`, `application`, `domain`, `domain/services`, `presentation`, `tools`, 최상위.
  설명이 없는 모듈 4개 (`adapters/storage/local_fs.py`·`memory.py`, `persistence/sqlalchemy/uow.py`, `presentation/dependencies.py`).
  "어디에 무엇이 있나" 를 알려 주는 문서는 `docs/ARCHITECTURE.md` §4 의 트리뿐인데 낡았다 (D-04: 어댑터 패키지 셋·`tools`·라우터 둘이 없다).
- 근거: 새로 온 사람이 "정체성 기준 이미지는 어디서 만드는가" 를 찾으려면 도메인 서비스·포트·파이프라인 단계·어댑터 네 곳을 스스로 이어야 한다
  (위 개념 지도의 한 줄). 패키지에 들어가도 무엇이 밖에서 쓰는 이름이고 무엇이 안쪽 구현인지 표시가 없다 — 예를 들어 `adapters/generator/` 는
  7모듈이 같은 높이에 있고 `__init__.py` 가 클래스 여덟 개를 내보내지만, 조립 코드는 그 입구와 안쪽 모듈(`ffmpeg`·`openrouter`·`cache`·`decorators`)을
  섞어서 import 한다 (`config/registry.py:83,148,188-189,216-217`, `config/container.py:15-16`).
- 제안
  1. **패키지의 입구**: 패키지마다 `__init__.py` 머리에 세 가지를 적는다 — 맡는 개념, 밖에서 쓰는 이름, 안의 모듈 지도.
     `__init__.py` 는 밖에서 쓸 이름만 내보내고 (`__all__`), 새로 만들거나 옮기는 패키지의 안쪽 구현 모듈은 `_` 로 시작한다.
     패키지 밖의 코드는 `_` 모듈을 import 하지 않는다 (테스트는 예외).
  2. **모듈 지도**: `docs/MODULE_MAP.md` (새 문서) — 개념 → 패키지 → 포트·주요 클래스. 위 표가 초안이다.
     `docs/ARCHITECTURE.md` §4 는 다른 브랜치가 고치고 있어, 머지 뒤 그 절이 새 문서를 가리키게 한다 (P-11).
  3. **지키는 검사**: 아키텍처 테스트가 '패키지에 설명과 `__all__` 이 있는가', '밖에서 `_` 모듈을 import 하지 않는가', '모듈 지도의 패키지 목록이
     실제와 같은가' 를 본다 (지금 없는 곳은 기준선 목록. §9 G2·G8).
- 담당·시점: **감사 · 2단계** (테스트) / 3단계 (설명 채우기 — 옮기는 패키지부터) · 상태: **일부 해결** (`681501d` — 검사 셋과 모듈 지도 `docs/MODULE_MAP.md`. 설명·`__all__` 은 기준선으로 남아 3단계에 채운다)

#### K-04 · 중간 · [섞임] `adapters/generator/` 한 패키지에 네 개념이 섞여 있다
- 어디: `src/frame/adapters/generator/` (7모듈 2,786줄) — 제공자 구현(`openrouter.py`·`fake.py`·`stubs.py`), 복원력 데코레이터(`decorators.py`:
  재시도·시간 제한·결과 캐시), 한 번만 과금하기 위한 기록(`cache.py`·`ledger.py`), 그리고 **다른 포트**인 조립기(`ffmpeg.py` 의
  `FfmpegVideoAssembler`, `fake.py` 의 `FakeVideoAssembler`).
- 근거: "최종 영상을 잇는 코드는 어디에 있는가" 의 답이 생성기 패키지다. 조립기를 등록하는 코드는 출력 크기(`output_size`)를 얻으려고
  OpenRouter 모듈을 import 한다 (`config/registry.py:216-217`). 클립 정규화(`FfmpegVideoNormalizer`)는 생성기의 부품이고 조립기는 별개 포트인데 한 파일에 있다.
  포트 쪽도 같다 — `application/ports/generator.py` 에 `VideoGenerator` 와 `VideoAssembler` 가 함께 있다 (`:153`, `:198`).
- 제안: `generator/openrouter/`(제공자) · `generator/resilience.py`(재시도·시간 제한·결과 캐시) · `generator/records/`(생성 캐시·작업 원장·키 잠금) ·
  `generator/normalizer.py`(클립 정규화) · `assembler/`(조립기 구현) 로 나눈다. ffmpeg 실행·영상 정보는 `shared/media/` 로 (R-03).
  조립기 포트는 `ports/assembler.py` 로 떼고 `ports/generator.py` 가 다시 내보낸다.
- 담당·시점: **감사 · 지금** (M-01 과 같은 묶음) · 상태: 열림

#### K-05 · 낮음 · [섞임·이름] 기술 종류나 방법으로 묶은 모듈
- 어디
  - 기술 종류로 묶은 것: `domain/enums.py` (열거형 9개 — 버전 상태·추적·검수·객체 종류·수정·참조의 것이 한 파일),
    `domain/services/` (도메인 서비스라는 형식으로 묶은 수정 전파·수정 요청 풀기·정체성·비교), `application/dto.py` (유스케이스 여덟 개의 입출력),
    `application/use_cases/queries.py` (읽기 전용이라는 이유로 프로젝트·버전·검수·추적·미디어·비교·대시보드 조회 아홉 개가 한 클래스),
    `presentation/schemas.py`·`mappers.py` (M-03), `adapters/generator/decorators.py`.
  - 방법으로 지은 이름: `adapters/common.py` (ID 생성기·시계 — 운영 구현과 테스트용 구현이 함께), `adapters/video_tools.py`, `adapters/vlm_answers.py`,
    `adapters/generator/stubs.py`.
- 제안: 옮길 때 개념 이름으로 — `shared/runtime.py`(ID·시계), `shared/media/frames.py`, `shared/vlm/answers.py`, `generator/resilience.py`.
  열거형은 그 개념의 모듈 옆으로 (수정의 셋은 `domain/revision/` 을 만들 때 함께. `enums.py` 는 다시 내보낸다).
  `domain/services/` 의 나머지 둘(`identity.py`·`comparison.py`)은 `domain/` 바로 아래로 올리고 `services/` 는 호환 경로로만 남긴다.
  `dto.py`·`queries.py` 는 자원별로 나눈다 (머지 뒤 — `dto.py` 는 #10 이 고치고 있고, `queries.py` 는 M-03 의 자원 구분과 함께 한다).
- 담당·시점: **감사** — 어댑터 쪽과 `domain/revision/` 은 지금, `dto.py`·`queries.py`·나머지 열거형은 머지 뒤 (P-12) · 상태: 열림

#### 목표 배치

```
src/frame/
  domain/
    ir.py · vocabulary.py · identifiers.py     구조화 결과(IR)와 술어 어휘
    models.py · state_machine.py · progress.py 프로젝트·버전·상태·진행
    revision/                                  수정 의도 · 속성 변경과 전파 · 수정 요청 풀기 (M-07)
    checks.py · metrics.py · comparison.py     검수 결과 · 성공 지표 · 버전 비교
    identity.py                                정체성 기준 이미지의 규칙
    tracking.py                                추적
    fingerprints.py                            해시·키의 정의를 한 곳에 (K-01, R-01)
    <새 개념>/                                 새로 들어오는 개념은 하위 패키지로 (예: 외부 숏폼의 테마·편)
  application/
    ports/                포트 하나가 모듈 하나 (조립기 포트는 assembler.py 로)
    use_cases/            유스케이스 하나가 모듈 하나. 입출력 타입은 그 모듈 옆에
    pipeline/             생성 → 추적 → 검수. 정체성 기준 뽑기는 identity_anchoring.py 로 (M-06)
  adapters/
    shared/               어댑터들이 함께 쓰는 것. 어댑터 패키지끼리는 여기만 import 한다 (L-03)
      media/              ffmpeg 찾기·실행 · 영상 정보 · 프레임 · SSIM
      vlm/                이미지를 읽는 모델에 묻고 답을 맞추기
      api_keys.py         키 파일 읽기 · 형식 · 가리기
      http.py · runtime.py
    generator/            장면 클립 만들기
      openrouter/         제공자: 제출 · 대기 · 원장으로 이어 받기 · 부모 클립 편집 · 미리보기 · 프롬프트 · 모델 정보 (M-01)
      resilience.py       재시도 · 시간 제한 · 결과 캐시
      records/            한 번만 과금: 생성 캐시 · 작업 원장 · 키 잠금
      normalizer.py       클립 정규화
    assembler/            클립을 이어 최종 영상
    checker/              검수
      vlm/                이미지를 읽는 모델로 판정 (공통 골격 + 요구 반영 · 일관성 · 유지 요소)
    identity/ · structurer/ · change_request/ · llm/ · tracker/ · persistence/ · storage/ · jobs/
  presentation/           schemas/ · mappers/ · routers/ — 자원별 (projects · versions · revisions · checks · comparison · dashboard · settings)
  config/                 settings.py · registry.py · registrations/<포트>.py · container.py
  evaluation/             edits/ · structuring/
  tools/                  demo/ · bundle/ · 명령 하나짜리 모듈
```

배치 규칙은 ADR-0031 로 남긴다.
1. 패키지 하나는 개념 하나다. 파일 크기나 기술 종류(스키마·열거형·데코레이터)로 묶지 않는다. 디렉터리가 깊어져도 된다.
2. 이름은 개념의 낱말로 짓는다 — 용어집(`docs/GLOSSARY.md`)의 낱말을 쓰고, `utils`·`common`·`helpers`·`tools` 같은 이름은 쓰지 않는다.
3. 패키지의 입구가 보이게 한다 — `__init__.py` 머리에 맡는 개념·밖에서 쓰는 이름·모듈 지도, `__all__` 로 공개할 것만, 안쪽 구현 모듈은 `_` 접두사.
4. 개념 → 패키지 → 포트·주요 클래스를 잇는 모듈 지도(`docs/MODULE_MAP.md`)를 코드와 함께 고친다.
5. 어댑터 패키지끼리는 `adapters.shared` 만 import 한다.
6. 옮긴 모듈은 옛 경로에서 다시 내보내고, 작업 중 브랜치가 모두 dev 에 들어간 뒤 걷어낸다.

### 5.2 큰 모듈

### M-01 · 중간 · `adapters/generator/openrouter.py` — 1,675줄, 클래스 하나가 1,091줄에 메서드 43개
- 어디: `src/frame/adapters/generator/openrouter.py:561-1651` `OpenRouterVideoGenerator`. 생성자 인자 24개 (`:564`),
  `compile_edit_prompt` 분기 20개 (`:439`).
- 한 클래스에 든 책임: 프롬프트 만들기(생성·편집), 모델 정보·가격, 요청 본문 조립, 제출·폴링·내려받기, 작업 원장으로 이어 받기,
  부모 클립 주소 구하기·들고 있기, 미리보기·어림값, 임시 결과 처리, 오류 문구 다듬기.
- 제안: `adapters/generator/openrouter/` 하위 패키지로 나눈다. 모듈은 이 제공자가 하는 일의 낱말로 짓는다.
  밖에서 쓰는 이름은 `__init__.py` 가 내보내고, 안쪽 모듈은 `_` 로 시작한다 (K-03).

  | 새 모듈 | 맡는 일 | 옮길 것 (지금의 줄) |
  |---|---|---|
  | `__init__.py` | 입구 | 지금의 공개 이름을 그대로 내보낸다: `OpenRouterVideoGenerator`, `VideoModelInfo`, `compile_video_prompt`, `compile_edit_prompt`, `output_size`, `resolve_openrouter_key`, `mask_key`, 문구 상수 |
  | `generator.py` | 장면 하나를 만드는 흐름 | `__init__`, `generate_scene`, `_generate_new`, `_accepted`, `_collect`, `profile` |
  | `_prompts.py` | 프롬프트 (새로 생성 · 부모 클립 편집) | 315-511 (`compile_video_prompt`, `compile_edit_prompt`, 문구 상수) |
  | `_submission.py` | 제출 | 요청 본문 `_generation_body` 781-817, 참조 이미지 1423-1460, 과금 계산 1078-1093, `_submit` 1463-1496 |
  | `_waiting.py` | 대기와 내려받기 | `_wait` 1498-1545, `_download` 1547-1579, 작업 상태 한 번 조회 (지금 973-987 과 1519-1535 에 따로 있다), 일시 오류 분류 (A-37) |
  | `_ledger_resume.py` | 원장으로 이어 받기 · 임시 결과 | `_resumable`·`_remember`·`_reuse` 1299-1355, `_reuse_regenerated`·`_redownload_provisional` 735-779, `_share_regenerated` 1268-1297 |
  | `_parent_edit.py` | 부모 클립 편집 | `_EditPlan`·`_ParentUrl`·`_HeldUrl` 171-197, `result_key` 859-873, `_edit_plan` ~ 부모 주소 구하기·들고 있기 875-1027, 편집 본문 `_edit_body` 1114-1168 |
  | `_preview.py` | 미리보기 (제작 방법 · 어림 비용·시간) | `preview` 1030-1076, 어림 시간 1095-1112 |
  | `_storing.py` | 정규화해서 저장 | `_normalize`, 클립 저장 키와 메타데이터 조립 (`_collect` 의 뒤 절반) |
  | `_model_catalog.py` | 모델 정보 (지원 여부 · 가격 · 최대 길이) | `VideoModelInfo` 252-309, `model_info`·`_fetch_model_info`·`_check_supported` 1373-1420 |
  | `_api_key.py` | 키 찾기 · 가리기 | `resolve_openrouter_key` 215-241, `mask_key` 244-246 (공용 부분은 `shared/api_keys.py`) |
  | `_output_size.py` | 출력 크기 | `output_size` 517-539, `_SHORT_SIDE` 130-139 (조립기도 쓴다) |
  | `_failures.py` | 오류 문구 다듬기 · 가리기 | `_JobUnrecoverable`·`_SubmitRejected` 153-168, `_scrub`·`_http_error`·`_gone`·`_hint`·`_error_message` 1628-1668 |
  | `_options.py` | 설정 묶음 | 상수 111-126·200-209, 생성자 검증 592-608, `_checked_base_url` 546-555 → `OpenRouterConfig` (인자 24개를 출력·API·폴링·편집 묶음으로) |

  의존 방향은 `generator → _ledger_resume·_parent_edit·_preview·_storing → _submission·_waiting·_model_catalog·_prompts → _failures·_api_key·_output_size·_options` 한쪽이다.
  `openrouter/__init__.py` 가 지금의 공개 이름을 모두 다시 내보내므로 다른 모듈·테스트의 import 는 그대로다 (테스트가 이 모듈의 비공개 이름을
  건드리는 곳은 없다). 큰 함수는 옮기면서 나눈다: `compile_edit_prompt`(조건 차이 여섯 갈래를 표로), `_generate_new`(편집 시도 / 새로 생성),
  `_collect`(대기·다운로드 / 정규화·저장 / 메타데이터 / 원장 마무리). 편집 기록 dict(문자열 키 8개, 세 곳에서 조립)는 데이터클래스로 바꾼다.
- 담당·시점: **감사 · 지금** (다른 브랜치가 건드리지 않았다) · 상태: 열림

### M-02 · 중간 · `config/registry.py` 의 `default_registry()` 가 250줄이다
- 어디: `src/frame/config/registry.py:72-318` (문장 75개, 복잡도 11). 포트 10종의 등록이 한 함수에 있다.
- 제안: `config/registrations/{llm,structurer,change_request,generator,identity,tracker,checker,storage,persistence,jobs}.py` 에
  `def register(reg: AdapterRegistry) -> None` 을 하나씩 두고 `default_registry()` 는 그것들을 차례로 부른다.
  어댑터를 더하는 사람은 자기 포트의 파일만 고친다 (지금은 모든 브랜치가 같은 함수를 고친다).
  `AdapterRegistry.build` 는 `Any` 를 돌려준다 — `build[T](port: type[T], ...) -> T` 로 타입을 준다.
- 담당·시점: **감사 · 지금** (§12.3 결정 3 — 사용자: 지금 분해) · 상태: **해결** (`5a98ae9` — 포트마다 등록 모듈 하나 `config/registrations/{llm,structurer,change_request,generator,assembler,identity,tracker,checker,storage,persistence,jobs}.py`, 팩토리들이 함께 기대는 것은 `_dependencies.py` (`artifact_store`·`job_handlers`·`ffmpeg_path`·`llm_client`·`vlm_client`). `default_registry()` 는 `register_defaults()` 를 부른다.
  분해 전후로 등록된 (포트, 키) 32개가 같은 것을 견줘 확인했고, 키 목록·키마다 만들어지는 것·팩토리가 같은 레지스트리에서 LLM 을 찾는 것을
  `tests/unit/test_adapter_registry.py` 로 고정했다. `frame.config.registry` 의 `AdapterRegistry`·`BuildContext`·`default_registry` 는 그대로다.)
  - 조립기 등록을 `assembler.py` 로 따로 뒀다 (제안의 10개에서 11개로) — 목표 배치의 `adapters/assembler/` 와 같은 경계다.
  - 남긴 것 ①: `build` 의 반환 타입. 포트가 추상 클래스라 mypy 가 `type[T]` 자리에 받지 않고(`type-abstract`), `UnitOfWork` 키는 저장 단위가 아니라
    그것을 여는 함수를, `off` 는 None 을 돌려준다. 반환 계약부터 고쳐야 하는데 부르는 곳이 `container.py` 다 (#10·theme-series 가 고치는 파일, P-02 와 함께).
  - 남긴 것 ②: `ARCHITECTURE.md`(§4 트리·§5 절차 3·§6 패턴 표)와 `TDD_WORKFLOW.md`(§4·§5)의 '`config/registry.py` 에 한 줄' 문장.
    #21·#10 이 고친 파일이라 머지 뒤에 고친다 (P-11). 그때까지는 `registry.py` 머리말이 새 자리를 가리킨다.
  - theme-series 의 등록은 머지 뒤 `config/registrations/series.py` 로 들어온다 (그 이름을 비워 뒀다). `config/theme_wiring.py` 의
    `ffmpeg()`·`llm()`·`vlm()` 는 그때 `_dependencies` 의 `ffmpeg_path`·`llm_client`·`vlm_client` 로 합친다.

### M-03 · 중간 · `presentation/schemas.py`(523줄)·`mappers.py`(493줄) 가 모든 API 를 한 파일에 담는다
- 어디: `src/frame/presentation/schemas.py`, `mappers.py`, `routers/versions.py`(260줄, 엔드포인트 10개).
- 제안: 자원별 하위 패키지 — `presentation/schemas/{common,projects,versions,revisions,checks,comparison,dashboard,settings}.py`,
  `mappers/` 도 같은 축. `routers/versions.py` 는 조회 / 수정·계획 / 미디어로 나눈다. `__init__` 이 다시 내보낸다.
- 담당·시점: **감사 · 머지 뒤** (#10 브랜치가 세 파일을 모두 고치고 있다. P-09) · 상태: 열림

### M-04 · 중간 · VLM 검수기 셋과 정체성 추출기가 같은 흐름을 네 번 쓴다
- 어디: `adapters/checker/vlm_requirement.py`(421줄)·`vlm_consistency.py`(390)·`vlm_preservation.py`(368), `adapters/identity/vlm_crop.py`(499).
- 근거: 세 검수기는 생성자 인자(`store, ffmpeg, llm, frames_per_scene, [threshold], max_side, timeout_s`)와 메서드 이름
  (`check` → `_check_scene` → `_frames` → `_ask` → `_outcome` → `_undetermined`)이 같다. 글자까지 같은 부분:
  표본 시각 `int(duration * (index + 0.5) / count)` (`vlm_requirement.py:195`, `vlm_preservation.py:228`),
  `complete_json(SYSTEM_PROMPT, "\n".join(lines), RESPONSE_SCHEMA, schema_name=..., timeout_s=..., images=...)` → `json.loads(...)[목록 키]`
  → `_BadAnswer` → `match_answers(items, list(ids), field="id")` → `{model, usage}` (`vlm_requirement.py:231-251`, `vlm_preservation.py:253-272`),
  클래스 `_BadAnswer`(셋 모두), 함수 `_words`(셋)·`_suffix`(둘). 검수기마다 진짜로 다른 것은 질문 문장·응답 스키마·판정 규칙이다.
  중복은 이미 서로 어긋나 있다: 일관성 검수만 `match_answers` 를 쓰지 않고 (A-22), 요구 반영의 미판정만 `predicate` 가 없고 (A-16),
  정체성 추출만 묻지 않은 이름을 거른다. `_BadAnswer` 는 `LLMError` 가 아니어서 `except LLMError` 에 잡히지 않는다.
- 제안: 공통 골격(Template Method)은 얇게 두고, 변하는 것은 훅과 함수로 남긴다.

  | 무엇 | 어디에 | 내용 |
  |---|---|---|
  | 공통 골격 | `adapters/checker/vlm/base.py` `VlmChecker` | `check()` = 단위 고르기 → 사전 가드(상속·재사용·영상 아님·추적 실패) → 프레임 모으기 → 질문 → 판정. `AdapterError` 와 예상 밖 예외 모두 미판정으로 (A-13). 생성자, 미판정 결과, '첫 결과에만 토큰 수' 를 여기에 둔다. 약 70줄 |
  | 변하는 축 (훅) | 검수기마다 | 단위(장면 / 객체와 등장 장면 / 다시 만든 장면), 질문(`VlmQuestion`: system·user·schema·images), 판정. 점수 규칙은 클래스가 아니라 함수 (`judge_share`, `judge_any`) |
  | 협력 객체 | `adapters/shared/media/frames.py` `FrameSampler` | 표본 시각 계산, 추출, 한 실행 안의 캐시 (R-10). 정체성 추출기는 골격을 상속하지 않고 이것과 아래 둘만 쓴다 |
  | 질의 함수 | `adapters/shared/vlm/ask.py` `ask_json` | `complete_json` → JSON 읽기 → `LLMBadResponse` → `(data, model, usage)`. `_BadAnswer` 세 벌을 없앤다 |
  | 응답 맞추기 | `adapters/shared/vlm/answers.py` | 지금의 `vlm_answers.py` + 판정값 정규화 + 글 다듬기 |
  | 결과 도우미 | `adapters/checker/outcomes.py` | 재사용 장면 결과, 이어지는 경고 구간 합치기(두 벌), `detail` 키 상수 |

  합치지 않는 것: 프롬프트·스키마 문장 (ADR-0018 이 문장 하나로 판정이 바뀐 사례를 적었고 테스트가 문자열을 고정한다), 응답 모양,
  점수 규칙 셋, 정체성 추출의 실패 정책과 박스 기하, SSIM 검수기(보존 검수기와 가드 둘만 나눈다).
  옛 모듈(`checker/vlm_requirement.py` 등)은 새 모듈을 다시 내보내는 얇은 모듈로 남긴다. 순서는 순수 도우미 → `ask_json` →
  `FrameSampler`(시각·라벨을 바이트까지 같게) → 골격 → 패키지 이동. 동작을 바꾸는 수정(A-13·A-21~23)은 그 뒤에 테스트와 함께 따로 한다.
- 담당·시점: **감사 · 지금** · 상태: 열림

### M-05 · 낮음 · `tools/demo.py`(815줄)·`tools/bundle.py`(565줄)
- 어디: `src/frame/tools/demo.py` — 순수 함수, HTTP, 프로세스·신호, Windows 작업 개체(ctypes), 명령 4개, 점검 시나리오, 인자 처리가 한 파일에 있다.
  `tools/bundle.py` 는 `demo` 의 비공개 이름 5개(`_say`, `_run`, `_node_major` 등)에 기댄다 (`bundle.py:95,193,203`).
- 두 파일이 같은 일을 따로 하는 곳: 플랫폼 판별 네 가지 (`demo.py:64-70`, `bundle.py:56-62,104-106`), 프로세스 실행 도우미 셋과 직접 호출 일곱 곳,
  화면 빌드 두 벌 (`demo.py:569-584`, `bundle.py:197-206`), 설치 대상 문자열 네 곳 (`.[llm]` / `.[dev,llm]` / `.`), 키 정규식 세 벌
  (`demo.py:45`, `adapters/llm/keys.py:8`, `presentation/routers/settings.py:29` — 길이 규칙이 서로 다르다).
- 제안: `tools/_console.py`·`_platform.py`·`_proc.py`·`frontend_build.py` (공용), `tools/demo/{env,keys,server,winjob,commands,smoke,__main__}.py`,
  `tools/bundle/{targets,runtime,stage,archive,verify,cli}.py`. `python -m frame.tools.demo` 진입과 `scripts/*.py` 의 import 는 그대로 둔다.
  `cmd_setup`(55줄, 분기 16, return 10)은 옮기면서 가상환경·설치·화면 빌드로 나눈다.
- 담당·시점: **감사 · 지금** (O-08·O-11·O-12 의 수정과 함께) · 상태: 열림

### M-06 · 낮음 · `GenerateStage` 가 세 가지 일을 한다
- 어디: `src/frame/application/pipeline/stages.py:49-247` — 재사용 판단(`_ready_clip`), 정체성 기준 이미지 뽑기(`_derive*`, 80줄),
  요청 조립·생성·조립.
- 제안: 정체성 기준 뽑기를 협력 객체(`IdentityAnchoring`)로 뺀다. 단계는 "재사용할 것 고르기 → 생성 → 조립" 만 남는다.
- 담당·시점: **감사 · 지금** (다른 브랜치가 건드리지 않았다. 우선순위는 M-01·M-04 뒤) · 상태: 열림

### M-07 · 낮음 · `domain/revision.py` 와 `domain/services/propagation.py` 가 서로를 필요로 한다
- 어디: `src/frame/domain/revision.py:123` (`change: Any  # AttributeChange (순환 import 를 피하려고 형을 적지 않는다)`),
  `:152` (함수 안 `from .services.propagation import target_scenes`).
- 근거: '수정 의도·변경' 이라는 한 개념이 `domain/revision.py`(의도·유지할 요소), `domain/services/propagation.py`(속성 변경·전파),
  `domain/services/revision_planning.py`(수정 요청 풀기)에 나뉘어 있고, 나뉜 자리 때문에 순환이 생겼다.
- 제안: `domain/revision/` 패키지로 모은다 — `intent.py`(수정 의도·유지할 요소), `changes.py`(속성 변경과 대상 장면), `propagation.py`(전파 정책),
  `resolution.py`(수정 요청 풀기·수정 버전 초안). `domain/revision/__init__.py` 가 지금 `domain/revision.py` 의 이름을 그대로 내보내고,
  `domain/services/propagation.py`·`revision_planning.py` 는 다시 내보내는 얇은 모듈로 남는다 (다른 브랜치의
  `from frame.domain.services.propagation import AttributeChange` 가 그대로 돈다). `Any` 두 곳에 타입이 돌아온다.
- 담당·시점: **감사 · 지금** · 상태: 열림

## 6. 재사용과 디자인 패턴

패턴은 실제 중복이나 변경 지점이 있는 곳에만 제안한다.

| ID | 심각도 | 무엇 | 어디 | 제안 (패턴) | 시점 |
|---|---|---|---|---|---|
| R-01 | 중간 | 장면 조건 해시의 내용이 두 군데서 조립된다. 장면에 필드를 더하면 둘을 같이 고쳐야 재사용 판단이 맞는다 | `domain/ir.py:284-308` `scene_fingerprint`, `application/ports/generator.py:81-104` `fingerprint` | 도메인 함수 하나(`scene_condition_payload(scene, entities)`)를 둘이 쓴다 | 머지 뒤 (`ir.py` 를 #10 이 고친다. P-08) |
| R-02 | 중간 | 생성기 데코레이터 셋이 `profile`·`result_key`·`preview` 위임을 각각 적는다 (9개 메서드). 포트에 선택 메서드를 더할 때 하나라도 빠뜨리면 기본 구현이 조용히 답한다 | `adapters/generator/decorators.py:44-52`, `:106-113`, `:175-184` | 위임 기반 클래스 `DelegatingVideoGenerator` (Decorator) | **해결** `3a6096b` (`DelegatingVideoGenerator`. 포트의 공개 메서드가 모두 위임되는지 보는 테스트) |
| R-03 | 중간 | ffmpeg 실행기(`_run`)와 로그 꼬리(`_tail`)가 글자까지 같은 두 벌이고, 영상 정보 해석도 두 군데다 | `adapters/generator/ffmpeg.py:308-338`, `adapters/video_tools.py:154-172`; `ffmpeg.py:40-50` `VideoProbe` vs `video_tools.py:115-124` `video_size` | `adapters/shared/media/` 한 곳: 실행·꼬리·정보 읽기·임시 파일. 임시 파일 이름에 `.bin` 을 쓰지 않는다 — ffmpeg 가 확장자로 'Binary text' 형식을 골라, `video_size` (`video_tools.py:118` 의 `media.bin`)에 줄바꿈 없는 글 800바이트를 주면 640×80 영상으로 읽힌다 (재현) | 지금 |
| R-04 | 낮음 | 클립 메타데이터의 공통 기록(모델·비용·시간·편집 여부)을 읽는 코드가 두 벌 (#21 이 세 벌째), `_number` 는 세 벌 | `presentation/mappers.py:172-195`, `application/use_cases/plan_revision.py:267-268` | 값 객체 `ClipProduction.from_metadata()` | 새 모듈과 `plan_revision.py` 는 지금, 나머지는 머지 뒤 (P-04) |
| R-05 | 낮음 | '수정할 수 있는 버전인가' 확인이 두 유스케이스에, 요청 → 명령 변환이 라우터 두 곳에 있다 | `create_revision.py:39-45`·`plan_revision.py:147-153`; `routers/versions.py:136-146`·`:179-197` | `VideoVersion.ensure_revisable()`; 변환은 `mappers` 로 | 앞은 지금, 뒤는 머지 뒤 (P-09) |
| R-06 | 낮음 | 시계 포트가 없을 때의 대체 구현(`datetime.now(UTC)` 람다)이 유스케이스 두 곳에 있다 | `accept_generation.py:26`, `run_generation_pipeline.py:56` | `Clock` 을 필수 인자로 (컨테이너는 이미 넘긴다) | 지금 |
| R-07 | 낮음 | 정규화 좌표 사각형이 둘 (theme-series 가 셋째 `Region` 을 더했다) | `adapters/video_tools.py:35-51` `Box`, `domain/tracking.py:12-17` `BoundingBox` | `Region` 하나로 | 머지 뒤 (P-07) |
| R-08 | 낮음 | 타임라인 연속성(0 ms 시작·빈틈·겹침 없음) 검사가 IR 에 있고, theme-series 의 샷 검사가 같은 규칙이다 | `domain/ir.py:142-159` | `domain/timeline.py` `ensure_contiguous` | 머지 뒤 (P-08) |
| R-09 | 낮음 | 예외 → (오류 코드, 메시지) 정규화가 파이프라인과 구조화에서 다르게 구현돼 있다 | `application/pipeline/runner.py:36-48`, `use_cases/structure_requirements.py:53-54` | 함수 하나 (`normalize_failure(exc)`) | 머지 뒤 (A-05 와 함께) |
| R-10 | 중간 | 검수기마다 같은 클립에서 같은 시각의 프레임을 다시 뽑는다. `extract_frame` 은 부를 때마다 클립 전체를 임시 파일에 쓰고 ffmpeg 를 띄운다 | `adapters/video_tools.py:100-112`; 요구 반영·일관성 검수가 둘 다 `(index + 0.5) / 3` 시각을 쓴다 | 프레임 표본 객체(시각 계산 + 실행 한 번 안의 캐시). 검수 시간(14~16초)의 일부가 여기서 나온다 | 지금 (M-04 와 함께) |

## 7. 클린코드

| ID | 심각도 | 무엇 | 어디 | 제안 | 시점 |
|---|---|---|---|---|---|
| C-01 | 낮음 | `ruff format --check` 미적용 8파일. 원인은 줄 끝 주석이다: 코드는 100칸으로 포맷하는데 한글 주석은 120칸까지 허용해서, 포맷터가 `bool  # 긴 주석` 을 괄호로 접는다 (`schemas.py` 의 세 곳이 그 예다). ruff 버전도 고정돼 있지 않다 (`pyproject.toml:19`) | `presentation/app.py`(4줄)·`schemas.py`(12), `tools/bundle.py`(171)·`demo.py`(84), 테스트 4개(33) | 긴 줄 끝 주석을 윗줄로 올린 뒤 포맷. ruff 버전 고정 | 6파일 지금, `presentation/app.py`·`schemas.py` 는 #10 (그 브랜치가 고친 파일. 전달 노트 12번) |
| C-02 | 낮음 | 켜지 않은 규칙을 가리키는 `# noqa` 13곳 (`BLE001` 8, `B027` 5 등). 이유 주석은 맞는데 규칙이 꺼져 있어 아무것도 막지 않는다 | `application/pipeline/stage.py:21-32,85`, `runner.py:45`, `recover_pending_versions.py:109,178` 외 | 규칙(`BLE`)을 켠다. 주석이 실제 예외 목록이 된다 | 2단계 |
| C-03 | 낮음 | `VideoVersion` 의 키워드 전용 필드 셋이 필수 필드 사이에 끼어 있다 (병렬 작업 때 충돌을 피하려던 흔적) | `domain/models.py:47-53` | 클래스 끝으로 옮긴다 (키워드 전용이라 동작은 같다) | 지금 |
| C-04 | 낮음 | 실행 중 불변식을 `assert` 로 확인한다 11곳 (`python -O` 에서 사라진다) | `run_generation_pipeline.py:70,109,125`, `accept_generation.py:42` 외 | 유스케이스의 것은 명시적 오류로. 나머지는 그대로 | 지금 |
| C-05 | 낮음 | 복잡도 10 초과 함수 12개 | `identity/vlm_crop.py:182` `extract`(19), `openrouter.py:439` `compile_edit_prompt`(17), `structurer/repair.py:63`(17), `change_request/llm.py:162`(14), `domain/ir.py:162`(14), `tools/demo.py:536`(14) 외 | 상한을 지금 최댓값(19)으로 걸고 내려간다 (§9 G4). 분해는 M-01·M-04·M-05 에서 | 2·3단계 |
| C-06 | 낮음 | 인자가 많은 생성자 | `openrouter.py:564`(24), `identity/vlm_crop.py:143`(13), `llm/openai_client.py:68`(13) | 설정 묶음 객체 (`OpenRouterConfig` 등). 레지스트리가 `Settings` 에서 한 번에 만든다 | M-01·M-04 와 함께 |
| C-07 | 낮음 | `__all__` 이 일부만 담는다 | `application/ports/__init__.py`(LLM·변경 요청 포트 없음), `ports/generator.py:205-213`(`ProductionMethod`·`ProductionPreview` 없음) | 맞춘다 | 머지 뒤 (`ports/__init__.py` 를 #10 이 고친다) |
| C-08 | 낮음 | 공유 `.venv` 의 Python 이 `3.11.0rc2` (출시 후보판) | 로컬 환경 | 3.11 정식판으로 다시 만든다 (§12.3 결정 6) | 결정 |
| C-09 | 낮음 | 예외 클래스가 세 군데에 흩어져 있다 | `domain/errors.py`, `domain/services/comparison.py:31` `VersionNotComparable`, `application/ports/change_request.py:20` | 도메인 오류는 `domain/errors.py` 로 | 머지 뒤 (#10 이 `errors.py` 를 고친다) |

## 8. 테스트

규모: 테스트 함수 785개, 수집 985개(+ live 2개) — unit 665 · contract 203 · api 75 · integration 28 · acceptance 14.
mock 라이브러리 사용 0건, `pytest.raises(Exception)` 0건, `xfail` 0건, 고정 포트 없음, 테스트가 저장소에 쓰는 파일 없음.
Fake 를 1급 어댑터로 쓰는 방침은 잘 지켜지고 있다. 빈 곳은 '실제 구성' 쪽에 몰려 있다.

### T-01 · 중간 · 기본 `pytest` 가 유료 테스트를 수집하고, 스위치가 서버 설정과 같은 환경 변수다
- 어디: `pyproject.toml:32` (`addopts = "-ra --strict-markers"` — 마커 필터 없음), `tests/live/test_openrouter_live.py:31`
  (`FRAME_GENERATOR == "external_api"`), `tests/live/test_openai_live.py:33` (`FRAME_STRUCTURER == "llm"`).
- 근거: `pytest --co` 는 live 2개를 포함해 987개를 수집한다. `README.md:32` 와 `docs/TDD_WORKFLOW.md:56` 은 '전체' 를 필터 없는
  `python -m pytest` 로 안내하고, `pyproject.toml:35` 는 slow 를 "(기본 제외)" 라고 적었다. 실제 생성기로 서버를 띄우던 셸
  (PowerShell 의 `$env:` 는 세션 내내 남는다)에서 문서대로 치면 OpenRouter 에 장면 하나를 제출한다 (테스트 주석 기준 약 $0.2~0.4).
  CI 는 `-m` 을 주고 비밀이 없어 지금은 안전하지만 그 한 줄에만 기댄다.
- 제안: `addopts` 에 `-m "not slow and not gpu"` 를 넣는다 (명령줄의 `-m slow` 가 뒤에 와서 이긴다).
  `tests/live/conftest.py` 가 `FRAME_LIVE_TESTS=1` 이 없으면 건너뛴다.
- 담당·시점: **감사 · 2단계** (G7) · 상태: **해결** (`3293469` — 기본 실행에서 slow 를 빼고 `FRAME_LIVE_TESTS=1` 스위치를 둔다)

### T-02 · 높음 · 실제 서버 구성인 스레드 디스패처에 테스트가 없다
- 어디: `src/frame/adapters/jobs/thread.py:13-34` (커버리지 44%, 생성자부터 미실행). `tests/` 에 `ThreadPoolJobDispatcher`·`jobs="thread"` 가 한 번도 나오지 않는다.
- 근거: `.env.example:20`, `docker/Dockerfile:12`, `README.md:101` 이 권하는 실행 방식인데 CI 는 `recording`·`inline` 만 돈다.
  그 조합에서 결함이 재현됐다 (A-41). 동시 접수(`try_transition` 경쟁)도 순차 테스트뿐이다 (이슈 #4).
- 제안: `tests/integration/test_thread_dispatcher.py` — `jobs="thread"` × {memory, SQLite 파일, in-memory SQLite}로 API 흐름을 조회 스레드와 함께
  돌리고 `dispatcher.wait()` 뒤 COMPLETED 를 단언한다. 핸들러 예외가 워커를 죽이지 않는 것, `shutdown()`, 동시에 `POST /generations` 2건이
  202 하나·409 하나인 것을 본다.
- 담당·시점: **감사 · 지금** (A-07·A-41 과 함께) · 상태: 열림

### T-03 · 중간 · ffmpeg 가 없으면 테스트의 약 10% 가 조용히 빠지고, CI 는 ffmpeg 를 설치하지 않는다
- 어디: `.github/workflows/ci.yml:17` (`pip install -e ".[dev,llm]"` — `video` extra 없음, ffmpeg 설치 단계 없음),
  `tests/support/video.py:12-16` (ffmpeg 를 못 찾으면 건너뛴다).
- 근거: ffmpeg 를 못 찾게 한 채 같은 실행을 돌리면 888 통과 · 97 건너뜀, 커버리지 90% → 81% 다 (측정). 빠지는 것은 실제 어댑터 테스트 전부다:
  VLM 검수기 셋, 정체성 추출기, 클립 정규화·조립, 루프백 OpenRouter 전체 흐름. 그 조건에서 `checker/vlm_*.py` 와 `identity/vlm_crop.py` 의
  커버리지는 22~25% 다. `ci.yml` 의 마지막 변경은 11-09 이고 ffmpeg 테스트는 11-12 에 들어왔다.
  러너 이미지에 ffmpeg 가 기본으로 없다는 점은 확인하지 못했다 (추정). 맞다면 CI 는 Fake 경로만 지키고 있다.
- 제안: CI 설치를 `.[dev,llm,video]` 로 바꾸고, CI 에서는 ffmpeg 가 없으면 건너뛰지 말고 실패하게 한다 (`FRAME_REQUIRE_FFMPEG=1`).
  보고서 머리에 ffmpeg 경로·버전과 건너뛴 수를 찍는다.
- 담당·시점: **감사 · 2단계** (가장 먼저) · 상태: **해결** (테스트 쪽 `3293469`, 워크플로 `7eec504`. GitHub 에서의 첫 실행은 아직 보지 못했다)

### T-04 · 중간 · 과금 뒤 경로의 분기가 미검증이다
- 어디: `src/frame/adapters/generator/openrouter.py:1558-1579` (다운로드 재시도 소진·404·그 밖의 상태), `:829` (202 인데 작업 id 없음), `:1610-1618` (JSON 아님).
- 근거: 기준선 커버리지의 빈 줄이다. 다운로드 테스트는 "409 한 번 뒤 성공" 과 "영상이 아님" 둘뿐이다. 코드는 재시도 소진을 재시도 불가로 올리고
  원장을 `submitted` 로 남겨 다음 요청이 이어 받게 한다. 이 구분이 뒤바뀌면 같은 장면을 다시 제출하는데 잡을 테스트가 없다.
- 제안: `tests/unit/generator/test_openrouter_ledger.py` 에 네 건 — 다운로드가 세 번 다 실패하면 원장 `submitted`·다음 요청은 POST 0회,
  다운로드 404 면 원장 `failed`·다음 요청 POST 1회, 다운로드 401 이면 이어 받기 가능, 202 본문에 id 가 없으면 재시도 데코레이터 아래에서 POST 정확히 1회.
- 담당·시점: **감사 · 지금** · 상태: **해결** (`fcd03cd` — 테스트 9개: 제안의 네 건에 응답 없음·JSON 아님·객체 아님을 더했다. 해당 분기 일곱 곳을 하나씩 뒤집어 모두 잡히는 것을 확인했다)

### T-05 · 중간 · 포트 계약 테스트의 빈칸
- 어디와 근거:
  - `ArtifactStore.exists()` — 다른 스킴·루트 밖 URI 에 메모리는 거짓, 로컬은 `StorageError` (`adapters/storage/local_fs.py:33-34,47-50`,
    `memory.py:24-25`). 계약 테스트는 같은 스킴의 없는 키만 본다 (`tests/contract/test_artifact_store_contract.py:33-38`).
    검수기·추출기·생성기 계약의 저장소가 전부 메모리라 A-21 이 드러나지 않았다.
  - Repository·UnitOfWork — commit 없이 닫은 쓰기가 메모리에서는 보이고 SQL 에서는 안 보인다. 저장 단위 안에서 예외가 나면 메모리는 전이가 남고
    SQL 은 롤백된다. 없는 버전의 `save()` 는 메모리가 `IntegrityViolation`, SQL 이 `LookupError` 다 (재현). 단위·API·인수 테스트 대부분이
    메모리 저장소 위에서 돌아, `commit()` 을 빠뜨린 유스케이스가 통과한다. 계약 docstring 이 말하는 `FRAME_TEST_DB_URL` 을 읽는 코드는 없다
    (`tests/contract/test_repository_contract.py:3`) — MySQL 방언은 한 번도 실행되지 않는다 (이슈 #5).
  - 계약 스위트가 없는 포트: `ChangeRequestInterpreter`, `JobDispatcher`, `LLMClient`. Checker 계약에는 요구 반영 계열과 `vlm_preservation` 이 없다.
    구조화 계약의 `fake` 는 기본이 규칙 기반 위임이라 같은 구현을 두 번 돈다 (`adapters/structurer/fake.py:20`).
    포트는 "실패: StructuringError" 라고 적었지만 LLM 구현은 `LLMTimeout` 을 그대로 올린다 (`application/ports/structurer.py:29-32`).
- 제안: `exists()` 계약을 두 구현 공통 테스트로 고정하고 로컬 구현을 맞춘다. 저장소 계약에 commit·rollback·예외 종류를 넣고 메모리 구현이 따르게 한다.
  검수기 계약의 저장소를 `params=["memory", "local_fs"]` 로 돌린다. 포트 문장만 담은 얇은 공용 스위트와 구현별 테스트를 나눈다.
- 담당·시점: **감사** — 저장소(`exists`)와 검수기 계약은 지금, Repository 계약은 머지 뒤 (#10 브랜치가 그 계약 테스트를 고치고 있다) · 상태: **일부 해결** (`f8cf87a` — `exists()` 계약을 두 구현 공통 테스트로 고정. 검수기 계약의 저장소를 두 구현으로 돌리는 것과 계약 스위트가 없는 포트는 남았다)

### T-06 · 중간 · 데코레이터의 `result_key` 전달이 검증되지 않는다
- 어디: `src/frame/adapters/generator/decorators.py:109-110,179-180`, `tests/contract/test_generator_contract.py:275-277,358-372`.
- 근거: 두 메서드를 지우고 생성기 계약·시간 제한·체인 테스트 74개를 돌려도 전부 통과한다. 계약 테스트의 데코레이터는 편집하지 않는 Fake 만
  감싸서 기본값과 결과가 같다. 전달이 빠지면 부모 클립이 다른 편집 결과가 같은 키로 캐시된다. 줄 커버리지는 100% 인데 동작은 묶여 있지 않다.
- 제안: 편집하는 생성기를 감싼 채 `wrap(inner).result_key(request) == inner.result_key(request)` 를 단언한다. R-02 의 위임 기반 클래스와 함께.
- 담당·시점: **감사 · 지금** · 상태: **해결** (`4eb7bf5` — 편집하는 Fake 를 감싼 데코레이터의 `result_key`·`profile`, 컨테이너 순서의 체인에서 부모 클립이 다른 두 편집이 따로
  캐시되는지. 세 데코레이터의 위임을 하나씩 지워 모두 잡히는 것을 확인했다. 위임 기반 클래스는 R-02 `3a6096b`)

### T-07 · 중간 · 테스트의 환경 격리가 파일마다 다르다
- 어디: `tests/unit/generator/test_openrouter_edit.py:594,606`, `tests/integration/test_consistency_api_flow.py:63`, `tests/unit/test_identity_config.py:45-46`.
  격리하는 곳은 `tests/unit/generator/test_openrouter_config.py:34-36` 와 `tests/api/test_llm_key_settings.py:17-19` 둘뿐이다.
- 근거: 키를 찾는 순서는 환경 변수가 키 파일보다 먼저다. 위 테스트들은 가짜 키 파일만 주고 `OPENROUTER_API_KEY`·`OPENAI_API_KEY` 를 지우지 않는다.
  개발자 셸에 실제 키가 있으면 테스트가 그 키를 루프백 가짜 서버에 보낸다. 지금은 외부로 나가지 않지만 주소 설정 한 줄이 빠지는 실수가 곧 유료 호출이 된다.
- 제안: 루트 `conftest.py` 에 autouse fixture 로 `FRAME_*`·`OPENAI_API_KEY`·`OPENROUTER_API_KEY` 를 지운다 (`slow` 는 제외).
  비-live 테스트에서 루프백이 아닌 소켓 연결을 막는다.
- 담당·시점: **감사 · 2단계** (G7 과 함께) · 상태: **해결** (`3293469` — 환경 변수 격리와 루프백 밖 연결 차단)

### T-08 · 중간 · live 테스트의 '키가 결과에 없다' 단언은 실패하면 키를 출력한다
- 어디: `tests/live/test_openrouter_live.py:85-86`, `tests/live/test_openai_live.py:65-66` — `assert key not in json.dumps(meta, ...)`.
- 근거: pytest 는 `in` 비교가 실패하면 양쪽 값을 보고서에 싣는다. 키 유출을 잡는 단언이 실패하는 순간 실제 키가 터미널과 로그에 남는다.
- 제안: `leaked = key in dumped` 로 먼저 계산하고 `assert not leaked, "키가 결과에 섞였습니다"` 로 쓴다.
- 담당·시점: **감사 · 지금** · 상태: 열림

### 그 밖의 테스트 발견 (낮음)

| ID | 무엇 | 어디 | 제안 | 시점 |
|---|---|---|---|---|
| T-09 | OpenAPI 계약 테스트가 이름의 집합만 비교한다 (경로·메서드·상태 코드·필드 이름). 필드 타입, `required`, enum 값, 파라미터 위치가 바뀌어도 통과한다. 저장된 `docs/openapi.json` 은 지금 코드로 만든 것과 바이트까지 같다 | `tests/api/test_openapi_contract.py:12-33`, `tools/export_openapi.py:32-85` | 서명에 타입·null 허용·`required`·enum 을 넣는다 | 머지 뒤 (#10 이 `openapi.json` 을 고친다) |
| T-10 | 느린 테스트. 전체 65.8초 가운데 검수기·정체성 계약 파일 둘이 19.5초 (프레임 추출을 테스트마다 다시 한다, 회당 48ms × 14회), `test_http_transport.py` 5.1초 (닫힌 루프백 포트 연결이 Windows 에서 2초, `serve_forever()` 기본 poll 0.5초 × 서버 2개), `test_consistency_api_flow.py` 10초 (테스트 3개가 각각 원본 3장면을 만든다). `docs/TDD_WORKFLOW.md:56` 은 "약 1초" 라고 적는다 | `tests/contract/test_checker_contract.py`, `tests/unit/test_http_transport.py:55-69`, `tests/support/openrouter_server.py:117` | `poll_interval=0.01` (약 3초), 프레임 추출 주입·메모이즈 (19.5초 → 3초 안팎, R-10 의 `FrameSampler`), 원본 생성을 모듈 fixture 로 | 지금 |
| T-11 | 테스트 구조. 큰 파일 셋 (`test_openrouter_edit.py` 1,126줄, `test_openrouter_generator.py` 703줄, `test_identity_extractor_contract.py` 578줄 — 계약은 그중 5개뿐). 도우미 중복 (`_request` 가 9개 파일, `store` fixture 가 11개 파일, 가짜 객체 `ProcessDied` 4곳). 계약 폴더에 구현별 테스트가, unit 폴더에 실제 ffmpeg 테스트가 섞여 있다. 프롬프트 문구를 단언하는 파일 13개, 대기 수열 단언 15곳. 비공개 속성 접근 (`store._blobs`, `generator._inner`). 인수 테스트 14개 중 1개에 AC 마커가 없고 마커 값은 검증하지 않는다. 복구의 경쟁 분기와 요청 제한의 창 만료가 미검증이다 (`recover_pending_versions.py:109-111,123,133`, `rate_limit.py:34-35`) | `tests/` 전반 | 파일 하나는 ADR 하나 또는 공개 함수 하나. `tests/support/{requests,api,vlm,doubles}.py` 로 모으기. 문구 단언은 프롬프트 단위 테스트에만 | 3단계 (M-01·M-04 와 같은 순서로) |

## 9. CI·운영 — 지금 검사하는 것과 더할 것

지금 `.github/workflows/ci.yml` 의 test 잡은 `ruff check`(E·F·I·B·UP)와 `pytest`(커버리지는 출력만)다. 그 밖에 frontend 잡,
세 OS 의 demo-smoke, `bundle.yml` 이 있다. format·타입·계층 규칙·커버리지 하한·보안 검사·문서 검사는 없다.

원칙: 새 게이트는 **지금 코드에서 통과하는 상태로** 들어간다. 걸리는 것은 기준선 목록(파일별 예외, 하한값)에 적고, 목록은 줄이기만 한다.
새로 쓰거나 고친 코드는 처음부터 규칙을 지킨다.

| ID | 게이트 | 지금 걸리는 양 | 기준선 방식 | 새 도구 |
|---|---|---|---|---|
| G0 | CI 에 ffmpeg (`video` extra), 없으면 건너뛰지 않고 실패 | 약 94개가 건너뛰어지는 것으로 보임 (T-03, 추정) | — | 없음 |
| G1 | `ruff format --check` + ruff 버전 고정 | 8파일 | 6파일은 고치고 `presentation/app.py`·`schemas.py` 는 제외 목록에 둔다 (#10 머지 뒤 뺀다) | 없음 |
| G2 | 구조 규칙 — pytest 아키텍처 테스트 (`tests/architecture/`): 계층 방향, 어댑터끼리는 `adapters.shared` 만, 패키지 설명과 `__all__`, 패키지 밖에서 `_` 모듈을 import 하지 않기, 모듈 크기 | 계층 0건. 설명 없는 패키지 10 · `__all__` 없는 패키지 8 · 500줄 넘는 모듈 4 | 계층은 예외 없음 (규칙은 §4 L-02 의 표). 나머지는 기준선 목록, 새 모듈은 500줄 이하 | 없음 |
| G3 | 커버리지 하한 | 전체 90.28%, domain+application 97% 대 | 전체 ≥ 90, 핵심(domain·application) ≥ 97. 내려가면 실패, 올라가면 하한을 올린다 | 없음 |
| G4 | ruff 규칙 확대 | 위반 0인 규칙군 15개는 바로. `BLE`·`SIM`·`C4`·`PTH`·`FURB`·`ISC` 는 모두 8건을 고치고. `S`(보안)는 src 32건, `T20` 은 도구 15건·live 테스트 7건, `C90` 은 상한 19 | 파일별 예외(`tools/*` 의 print·subprocess, 테스트의 assert)와 줄 단위 `# noqa: Sxxx - 이유` | 없음 |
| G5 | 타입 검사 (mypy) | 지금 설정으로 24건 (`arg-type` 13 · `index` 4 · `union-attr` 3 · `assignment` 2 · `import-untyped` 1 · `attr-defined` 1). 읽어 보니 모두 형 좁히기·시그니처 표기이고 실행 결함은 없다. `--strict` 는 42건 | 전체(`src`)를 지금 설정으로 켠다. 24건 가운데 다른 브랜치가 고친 파일의 3건(`config/container.py` 2 · `persistence/sqlalchemy/repositories.py` 1)은 파일별 예외로 두고 나머지는 고친다. `domain` 은 `--strict` 로 켠다 (0건). 나머지 패키지의 strict 는 기준선 42건에서 줄여 간다 | **mypy 2.4.1** (설치함. dev extra 에 버전을 고정한다) |
| G6 | 의존성 취약점 (`pip-audit`) | 미측정 | 별도 잡, 처음에는 실패해도 막지 않음 | CI 에서만 설치 |
| G7 | 유료 테스트 보호와 테스트 환경 격리 | T-01, T-07 | `addopts` 에 `-m "not slow and not gpu"` 기본, `tests/live/conftest.py` 가 `FRAME_LIVE_TESTS=1` 없으면 건너뜀 | 없음 |
| G8 | 문서 검사 — ADR 번호 중복·누락, `ARCHITECTURE.md` §12 목록, 모듈 지도(`docs/MODULE_MAP.md` 의 패키지 목록이 실제와 같은가) | ADR 목록 누락 4건(0010·0011·0012·0014), 모듈 지도 낡음 | pytest 테스트. 알려진 누락은 기준선 목록 | 없음 |
| G9 | 워크플로 정리 — `permissions: contents: read`, 같은 브랜치의 앞선 실행 취소, pip 캐시, `timeout-minutes`, 커버리지 XML 산출물, Python 3.11·3.12 와 Windows 한 칸, 릴리스가 `ci` 를 요구 (O-01·O-06·O-07) | — | — | 없음 |
| G10 | 로컬에서 같은 검사 — `scripts/check.py` 한 번에 (`PYTHONPATH=src` 를 준다, O-10), 문서 `docs/CI.md` | — | — | 없음 |

### 2단계 결과 (2026-11-14)

게이트를 모두 넣었다. 결정은 [ADR-0030](../adr/0030-ci-quality-gates-and-baselines.md), 쓰는 법은 [docs/CI.md](../CI.md) 에 있다.
로컬에서는 `python scripts/check.py` 가 전부 통과한다 (테스트 1011 통과 · 3 건너뜀 · 2 제외).
**워크플로는 push 하지 않아 GitHub 에서 돌려 보지 못했다** — 첫 push 뒤 확인할 것은 `docs/CI.md` 6절에 있다.

| 게이트 | 커밋 | 한 것 | 남은 것 |
|---|---|---|---|
| G0 ffmpeg | `3293469`, `7eec504` | CI 가 `video` extra 를 설치하고 `FRAME_REQUIRE_FFMPEG=1` 로 돈다. 없으면 건너뛰지 않고 실패 | 실제 영상 테스트 약 94개가 CI 에서 처음 돈다 — 첫 실행 확인 |
| G1 포맷 | `f159528` | 6파일 포맷, ruff 0.17.4 · mypy 2.4.1 고정 | `presentation/app.py`·`schemas.py` 는 기준선 (#10 머지 뒤) |
| G2 구조 규칙 | `681501d` | 계층 방향, 어댑터 경계, 패키지 입구, 모듈 크기 (pytest 11개). 위반을 심어 잡히는 것을 확인 | 기준선: 설명 없는 패키지 10 · `__all__` 없는 패키지 8 · 500줄 넘는 모듈 4 · `adapters/shared/` 밖의 공용 모듈 4 |
| G3 커버리지 | `a40a801` | 하한 전체 89 · 제품 코드 95 · 핵심 97 (측정 90.3 · 95.6 · 97.7) | 하한은 Windows 측정에 여유를 둔 값 — ubuntu 값을 본 뒤 맞춘다 |
| G4 ruff 규칙 | `6bdd91c` | 15군(위반 0) + 6군(8건 수정) + S · T20 · C90 | S101 기준선 9파일, 복잡도 상한 19 |
| G5 타입 | `fe5e1c7` | `mypy` 통과 — 24건 가운데 21건 수정, domain 은 strict 수준 | 기준선 3건 (다른 브랜치가 고치는 2모듈). strict 는 42건에서 줄여 간다 |
| G6 취약점 | `7eec504` | pip-audit 잡 (실패해도 막지 않음) | 결과를 본 뒤 막을지 정한다 |
| G7 유료 테스트 보호 | `3293469` | 기본 실행에서 slow 제외, `FRAME_LIVE_TESTS=1`, 환경 격리, 루프백 밖 연결 차단 | — |
| G8 문서 | `681501d` | ADR 색인 `docs/adr/README.md` 와 모듈 지도 `docs/MODULE_MAP.md` 를 새로 쓰고 실제 파일과 견준다 | `ARCHITECTURE.md` §4·§12 가 새 문서를 가리키게 (머지 뒤 P-11) |
| G9 워크플로 | `7eec504` | 권한 `contents: read`, 같은 ref 의 앞선 실행 취소, pip 캐시, 시간 제한, compat 잡(3.12 · Windows), release 가 `ci` 를 요구 | push·PR 이중 실행, 액션 SHA 고정은 제안으로 남김 |
| G10 로컬 검사 | `a40a801`, `fda450c` | `scripts/check.py`, `docs/CI.md` | — |

표로만 적었던 발견의 상태: C-01 6파일 해결(`f159528`) · C-02 해결(`6bdd91c`) · C-05 상한만(`6bdd91c`, 분해는 3단계) ·
O-02 개발 도구만 고정(`f159528`) · O-06 일부(`7eec504`) · O-07 해결(`7eec504`, 첫 실행 미확인) · O-10 해결(`a40a801`) ·
O-14 일부(`3293469` strict-config·xfail_strict, `fe5e1c7` mypy 대상, `a40a801` .gitignore. `.gitattributes` 는 남김) ·
D-04 일부(`681501d` ADR 색인을 따로 둠. 본문은 머지 뒤).

2단계 뒤에 찾아 고친 것: `scripts/check.py` 가 출력을 파일이나 파이프로 돌린 한국어 Windows(cp949)에서, 테스트가 다 통과한 뒤 커버리지 줄을
찍다가 `UnicodeEncodeError` 로 죽었다 (줄에 든 긴 줄표가 cp949 에 없다. 콘솔에 바로 찍을 때와 CI 의 ubuntu 에서는 나지 않는다).
`ca7389a` 로 고쳤고, 그때까지 테스트가 없던 판정 부분(커버리지 하한 통과·미달·읽지 못함, 실패한 검사 이름, cp949 출력)을 테스트 7개로 적었다.

2단계에서 달라진 계획: G1 의 기준선은 2파일(`app.py`·`schemas.py`)이다. G3 는 전체 하한을 90 이 아니라 89 로 두고 제품 코드(tools 제외) 95 를 더했다 —
tools 에 OS 별 코드가 있어 전체 값이 러너에 따라 흔들린다. G5 는 `domain`·`application` 부터가 아니라 `src` 전체를 켰다 (오류가 24건뿐이었다).

### O-01 · 높음 · 릴리스 번들이 테스트 게이트 없이, 고정되지 않은 의존성으로 만들어진다
- 어디: `.github/workflows/bundle.yml:6-9` (`push: tags: ["v*"]`), `:57-81` (release 잡은 `needs: build` 뿐, `gh release upload … --clobber`),
  `.github/workflows/ci.yml:4-5` (브랜치 push 에만 돈다), `src/frame/tools/bundle.py:217-218` (`pip install ".[llm]"`), `pyproject.toml:11-20`.
- 근거: 태그 push 는 `ci.yml` 을 돌리지 않는다. release 잡은 번들 빌드와 오프라인 자동 점검만 요구하고, 태그는 어떤 커밋에든 달 수 있다.
  번들 안의 의존성은 빌드 시점의 최신이다 — 공유 `.venv` 에서 `openai>=1.50` 은 3.46.0, `pytest>=8` 은 9.2.0 으로 풀려 있다.
  자동 점검은 오프라인 흐름과 키 입력만 보므로, 기본 시연 모드인 LLM 호출이 새 SDK 로 깨져도 릴리스가 나간다.
  `--clobber` 는 같은 태그의 자산을 조용히 바꾼다. 태그가 아직 하나도 없어 이 경로는 한 번도 돌지 않았다.
- 제안: `ci.yml` 에 `workflow_call` 을 더하고 release 를 `needs: [ci, build]` 로 바꾼다. 잠금 파일(해시 포함)을 만들어 번들·CI·Docker 가 같은 파일로 설치한다.
  태그가 `v{__version__}` 인지 확인하고 `--clobber` 를 뺀다.
- 담당·시점: **감사 · 2단계** (워크플로) / 잠금 파일은 결정 (§12.3 결정 7) · 상태: **일부 해결** (`7eec504` — release 가 같은 커밋의 `ci` 를 요구하고 태그가 패키지 버전으로 시작하는지 본다. 의존성 고정은 결정 7 을 기다리고, `--clobber` 는 그대로 두었다 — 문서의 태그 예시가 `v0.1.0-demo` 라 다시 올리는 흐름을 막지 않았다)

### 운영·CI 의 그 밖의 발견

| ID | 심각도 | 무엇 | 어디 | 제안 | 시점 |
|---|---|---|---|---|---|
| O-02 | 중간 | 의존성 고정 방식이 섞여 있고 잠금 파일이 없다. `fastapi==`·`uvicorn==` 은 고정, `sqlalchemy>=2.0,<3`·`pymysql>=1.1`·`openai>=1.50` 은 범위, 개발 도구(`ruff`, `mypy`, `pytest-cov`)는 무제한이다. 직접 import 하는 pydantic·starlette 는 선언이 없다. CI·Docker·번들·로컬이 각자 다른 조합으로 돈다 | `pyproject.toml:11-24` | 범위는 pyproject 에, 정확한 버전은 잠금 파일에. 개발 도구 버전 고정, `pydantic>=2,<3` 명시 | 2단계 (도구 고정) / 결정 (잠금 파일) |
| O-03 | 중간 | compose 가 MySQL 을 `root/root` 로 모든 인터페이스에 열고 (`3306:3306`), API 8000 도 인증·요청 제한 없이 연다. 건강 검사 `mysqladmin ping` 은 초기화 중에도 통과할 수 있다 (추정). `pymysql` 에 `[rsa]` 가 없어 MySQL 8.4 의 기본 인증에서 첫 접속이 실패할 수 있다. DB 볼륨이 없어 `down` 뒤 DB 는 비고 산출물만 남는다. `.dockerignore` 가 없다 | `docker/docker-compose.yml:5-15,27-36`, `pyproject.toml:15` | `127.0.0.1:` 로 묶기, 비밀번호는 `${VAR:?}`, `pymysql[rsa]`, `db_data` 볼륨, 허용 목록 방식 `.dockerignore` | 지금 (docker/ 는 다른 브랜치가 건드리지 않는다) |
| O-04 | 중간 | Docker 이미지가 초기 스켈레톤 그대로다. extra 없이 설치해 `FRAME_STRUCTURER=llm`·`external_api`·VLM 검수기·정체성 추출이 기동에서 실패한다 (compose 주석은 쓸 수 있다고 안내한다). root 로 돌고, HEALTHCHECK 가 없고, 화면이 없고, CI 에 docker build 가 없다. 단독 실행은 A-41 의 조합이다 | `docker/Dockerfile:1-15` | `pip install ".[llm,video]"`, 비 root 사용자, `/health` HEALTHCHECK, 파일 DB 기본값, CI 에 `docker build` (이슈 #6) | 지금 |
| O-05 | 중간 | 시연 런처가 `FRAME_JOBS=inline` 으로 띄운다. 분석 중에 끝내면 STRUCTURING 버전이 남고, 다음 실행의 기동 복구가 그 작업을 요청을 받기 전에 동기로 돌린다. LLM 호출이 60초 × 3(SDK 기본 재시도)까지 걸리면 90초 건강 대기를 넘겨 런처가 서버를 죽인다. STRUCTURING 재실행에는 횟수 상한이 없어 매번 반복된다 | `tools/demo.py:126,448`, `presentation/app.py:86-93`, `recover_pending_versions.py:85` | 기동 복구를 백그라운드로 옮기거나 재시도를 0으로 (A-24), 실패 안내에 `--offline`·`--reset` | 재시도 상한·런처 안내는 지금. 기동 복구를 옮기는 것은 머지 뒤 (`presentation/app.py` 를 #10 이 고쳤다) |
| O-06 | 중간 | `ci.yml` 에 권한 선언이 없다 (저장소 기본값에 따라 쓰기 토큰). 같은 저장소의 `feature-*` PR 은 push 와 pull_request 가 둘 다 돌아 잡이 두 배다 (macOS·Windows 포함). 동시성 취소·pip/npm 캐시·timeout 이 없다 (기본 360분). 액션이 메이저 태그로만 고정돼 있다 | `.github/workflows/ci.yml:3-19` | `permissions: contents: read`, `concurrency`, push 는 `[main, dev]` 만, 캐시, timeout 10분 | 2단계 (G9) |
| O-07 | 중간 | 단위 테스트가 ubuntu · 3.11 에서만 돈다. 번들과 demo-smoke 는 3.12, 로컬은 3.11.0rc2 다. Windows 전용 코드(작업 개체 약 60줄)의 유일한 테스트는 CI 에서 돌지 않고, 실패해도 `suppress(Exception)` 이 삼킨다. 로컬 3.11.0rc2 에는 tarfile 필터가 없어 번들 테스트 2개가 건너뛰어진다 | `.github/workflows/ci.yml:11-16`, `tools/demo.py:359`, `tests/unit/test_demo_tool.py:258` | test 잡에 `python: [3.11, 3.12]` 와 windows-latest 한 칸 | 2단계 |
| O-08 | 중간 | Windows 번들의 `README.txt` 줄바꿈이 `\r\r\n` 이 된다 (`\n` 을 `\r\n` 으로 바꾼 글을 텍스트 모드로 써서 한 번 더 바뀐다). `.sha256` 은 CRLF 로 써져 `sha256sum -c` 가 파일 이름 끝의 `\r` 때문에 실패한다 | `tools/bundle.py:358,413` | `write_bytes` 로 쓰고 테스트는 바이트로 단언 | 지금 |
| O-09 | 중간 | 번들에서 `setup` 을 하면 키가 번들 폴더 안(`.demo/openai.key`)에 평문으로 남는다. 준비해 둔 폴더를 USB·드라이브로 넘기면 키가 따라간다. Windows 에서는 폴더 ACL 을 그대로 따른다 | `tools/demo.py:209-221,594-604` | 번들 모드의 키는 사용자 프로필 아래에. 최소한 안내문에 "폴더를 넘기기 전에 `.demo` 삭제" | 지금 |
| O-10 | 중간 | 공유 `.venv` 의 편집 설치가 메인 체크아웃을 가리킨다. worktree 루트에서 `python -m frame.…` 을 치면 다른 브랜치의 코드가 돈다 (재현: `frame.__file__` 이 메인 체크아웃). pytest 만 `pythonpath = ["src"]` 덕에 worktree 를 본다. `README.md:85` 의 `python -m frame.tools.export_openapi` 를 worktree 에서 그대로 치면 다른 코드로 `docs/openapi.json` 을 덮어쓴다 | 로컬 환경, `pyproject.toml:30` | 로컬 검사 스크립트(G10)가 `PYTHONPATH=src` 를 준다. 문서에 한 줄 | 2단계 |
| O-11 | 낮음 | `--lan` 이 셸 환경의 값을 그대로 물려받는다. 요청 제한은 `setdefault` 라 `FRAME_RATE_LIMIT_PER_MINUTE=0` 이 있으면 꺼진 채 열리고, `FRAME_LLM_RUNTIME_KEY_ALLOW_REMOTE=true`·`FRAME_GENERATOR=external_api` 도 통과한다 | `tools/demo.py:122,139-140` | lan 이면 0·빈 값을 기본 제한으로, 원격 키 입력은 끈다. 유효한 설정을 안내에 찍는다 | 지금 |
| O-12 | 낮음 | 런처·번들의 가장자리. `setup` 이 대상 폴더가 FRAME 저장소인지 확인하지 않고 `.venv` 를 `--clear` 한다. `run --reset` 이 포트 확인보다 먼저 지운다. `smoke --port N` 은 이미 떠 있는 서버에 점검을 돌려 실제 DB 에 프로젝트를 만들고 화면에서 넣은 키를 지운다. 내려받기 실패·빈 `.sha256`·깨진 zip 이 traceback 으로 끝난다. subprocess 9곳에 timeout 이 없다. POSIX 에서 런처가 강제 종료되면 서버가 남는다 | `tools/demo.py:551-556,636-641,448-457,708-711`, `tools/bundle.py:161,479` | 저장소 확인, 순서 바꾸기, 포트 검사, 오류를 메시지로, timeout | 지금 (M-05 와 함께) |
| O-13 | 낮음 | 기본 브랜치(`main`)에 워크플로 파일이 없어 `workflow_dispatch` 수동 실행을 쓸 수 없다 (`docs/DEMO.md` 는 수동 실행으로 시험 빌드를 안내한다). Dependabot·스케줄도 기본 브랜치 기준이다 | 저장소 설정 | 기본 브랜치를 `dev` 로 바꾸거나 `main` 에 병합 | 결정 (§12.3 결정 5) |
| O-14 | 낮음 | 설정 파일. `.gitattributes` 가 `.sh`·`.cmd` 두 줄뿐이라 나머지 텍스트는 기기의 `core.autocrlf` 를 따른다 ("LF will be replaced by CRLF" 경고의 원인). `.gitignore` 에 `.env.*`·`coverage.xml`·`*.sqlite*` 가 없다. `[tool.mypy]` 에 대상이 없어 `mypy` 만으로는 돌지 않는다. pytest 에 `--strict-config`·`xfail_strict` 가 없다. 서버는 `-I` 로 떠서 런처가 넘기는 `PYTHONUTF8` 을 무시한다 (실제로 일하는 것은 `-X utf8`). 번들·런처는 `[video]` 를 설치하지 않아 실제 영상 경로를 쓸 수 없다 | `.gitattributes`, `.gitignore`, `pyproject.toml:29-57`, `tools/demo.py:132`, `tools/bundle.py:218` | `* text=auto eol=lf`, 무시 목록 보강, `files = ["src"]`, 죽은 설정 삭제 | 2단계 |

`tools/` 의 ruff 보안 경고(S603·S310·S607·S202·S104 14건)는 하나씩 보았고 실제 문제는 없다: subprocess 는 모두 리스트 인자에 상수뿐이고,
내려받는 파일은 고정 SHA-256 으로 검증하고, tar 는 `filter="data"` 로 풀고 필터가 없는 Python 은 거부한다. 게이트를 켤 때는 이유를 적은 `# noqa` 로 둔다.

GitHub 저장소 설정(브랜치 보호, 필수 검사)은 바꾸지 않는다. 제안은 §12.3 결정 5 에 있다.

## 10. 문서와 코드의 어긋남

맞는 것부터: 상태 기계(§3.3 다이어그램의 전이 7개), `.env.example` 의 53개 키 가운데 46개 값, 레지스트리 키 32개, ADR 0011~0021 이 적은 상수
(재시도 횟수·폴링 간격·기한·경로)는 코드와 같다. `docs/openapi.json` 은 지금 코드로 만든 것과 바이트까지 같다. ADR 번호는 0001~0021 연속이다.

| ID | 심각도 | 문서가 말하는 것 | 코드가 하는 것 | 어디 | 고칠 쪽 | 시점 |
|---|---|---|---|---|---|---|
| D-01 | 중간 | "`.env` 의 설정 키를 바꾼다", "`.env` 에 빈 값으로 남겨 둔 항목은 기본값" | `.env` 를 읽는 코드가 없다 (`dotenv`·`env_file`·`--env-file` 0건). `Settings.from_env` 는 `os.environ` 만 본다. `.env.example` 을 `.env` 로 복사해도 기본값(메모리 DB, Fake, 요청 제한 0)으로 뜬다 | `docs/ARCHITECTURE.md:300`, `docs/TDD_WORKFLOW.md:41`, `config/settings.py:98-104,155` | 문서에 읽는 방법을 적는다 (셸 export 또는 `uvicorn --env-file`). "`/health` 의 adapters 로 확인" 한 줄 | 머지 뒤 (P-11) |
| D-02 | 중간 | `.env.example` 의 예시는 `sqlalchemy` + MySQL 주소다. 주석들은 "운영은 Alembic 마이그레이션" 이라고 한다 | 테이블은 SQLite 이거나 `FRAME_DB_CREATE_SCHEMA` 일 때만 만든다 (`config/registry.py:306-310`). 그 변수는 `.env.example`·README 에 없다. Alembic 은 저장소에 없다. 예시대로면 기동은 되고 첫 쓰기 요청이 500 이다 (코드 경로로 판단). `FRAME_API_PREFIX` 도 문서에 없다 | `.env.example:18-19`, `config/settings.py:24,90` | `.env.example` 에 두 키를 더하고 주석을 "Alembic 은 아직 없다" 로 (이슈 #5) | 머지 뒤 (#10 이 `.env.example` 을 고쳤다. P-11) |
| D-03 | 중간 | 설정 표가 `FRAME_TRACKER` 를 `fake · opencv`, `FRAME_JOBS` 를 `inline · thread · recording` 으로 안내한다. "`FRAME_GENERATOR=diffusers` 처럼 키만 바꾸면 교체된다" | `opencv`·`diffusers` 는 늘 오류를 내는 뼈대다 (A-31). `recording` 은 테스트용이라 서버에서는 작업이 실행되지 않는다 | `README.md:64,73`, `docs/ARCHITECTURE.md:430`, `docker/docker-compose.yml:25` | 표에 '(뼈대)'·'(테스트용)' 을 적는다 | 머지 뒤 (P-11) |
| D-04 | 중간 | `ARCHITECTURE.md` 의 낡은 절 | §12 ADR 목록에 0010·0011·0012·0014 가 없다. §4 트리에 `identity`·`change_request`·`llm` 어댑터, `tools`, `routers/settings`·`vocabulary` 가 없다. §8 API 표는 13행인데 코드는 18개 오퍼레이션이다 (빠진 것: `revision-plans`, `reference-images`, `settings/llm-key` 셋). "Endpoint Catalog 7개". §3.6 오류 모델에 코드 10여 개와 상태 400·403·429·500·502·503 이 없다. §5 의 "`LLMStructurer._complete` 구현" 은 없는 확장 지점이다. §2 의 유스케이스 "6개" 는 8개다. §7 에 확장 열 9개가 없다 | `docs/ARCHITECTURE.md:35,52,209-217,247,283,334-338,359-373,388,438-454` | 문서 | 머지 뒤 (P-11). §12 목록은 G8 테스트가 지킨다 |
| D-05 | 낮음 | "모든 오류 응답은 `{error_code, error_message}`" | 응답은 그렇다. 명세가 다르다: 경로 매개변수만 있는 9개 오퍼레이션의 422 가 FastAPI 기본 `HTTPValidationError` 로 적혀 있고, `revision-plans` 의 502·503 과 공통 405·500 이 없다 | `docs/openapi.json`, `presentation/errors.py:89-129` | 라우트에 `responses={422: ErrorOut}` 선언, 명세 재생성 | 머지 뒤 (#10 이 라우터·명세를 고친다) |
| D-06 | 낮음 | `STRUCTURING_DESIGN.md` §2 "현재 코드 상태와 간극" — `LLMStructurer` 는 "모델 호출 없음, 보정·재시도 없음" | 호출·스키마·보정·재시도는 구현돼 있다 | `docs/STRUCTURING_DESIGN.md:43-54,108-128` | 문서 (#17 브랜치가 고치는 중) | #17 |
| D-07 | 낮음 | ADR 의 '결과·남은 일' 이 뒤의 ADR 로 바뀐 것을 표시하지 않는다 | 0011 "프로세스 메모리 캐시" (0013 에서 영속), 0013 "SQL 저장소는 클립 metadata 를 저장하지 않으므로" (0016), 0013 원장 상태 3개 (0021 의 `released`), 0002 의 키 형식 (0007), 0007 값 종류 5개 (0008 의 LITERAL) 등 8곳. 21개가 모두 '채택' 이라 부분 대체를 적을 방법이 없다 | `docs/adr/0002,0006,0007,0009,0011,0012,0013` | ADR 마다 "보완 (날짜): ADR-00NN" 한 줄 | 지금 (ADR 파일은 다른 브랜치가 건드리지 않는다) |
| D-08 | 낮음 | 숫자·이름·주석 | "전체 (약 1초)" 는 66초다. `_resolve_targets`·`PreservationChecker` 는 없는 이름이다. README 의 브랜치 전략에 `dev` 가 없다. `FRAME_VLM_MODEL` 의 사용처가 "vlm_crop · vlm_consistency" 로만 적혀 있다 (요구 반영·유지 요소 검수기도 쓴다). `FRAME_LLM_MAX_ATTEMPTS` 는 '재시도 횟수' 가 아니라 총 시도 횟수다. 낡은 코드 주석: `ci.yml:22` "dev 에는 아직 없어", `config/registry.py:73` "어댑터 모듈은 여기서만 import", `registry.py:133` 의 오류 문구 (VLM 검수기에서도 뜬다) | `docs/TDD_WORKFLOW.md:33,56`, `README.md:115`, `.env.example:38,48`, `config/settings.py:38-39,86` | 문서·주석 | 코드 주석은 지금, 문서는 머지 뒤 |

쓰이지 않는 코드 (C-10 · 낮음): `application/errors.py:33` `CheckError` (던지는 곳이 없다), 전이 `QUEUED → FAILED`
(`domain/state_machine.py:15` — 쓰는 경로가 없다), `adapters/llm/openai_client.py` 의 `resolve_api_key`·`key_hint` (테스트만 쓴다),
`adapters/structurer/llm_prompt.py:15` `PROMPT_VERSION` (src 에서 쓰는 곳이 없다. #17 의 골드셋 기록이 쓰기 시작했다).
쓸 계획이 있는 것은 그대로 두고 주석에 그 계획을 적는다. 담당·시점: 감사 · 3단계 (`state_machine.py` 는 #10 이 고친 파일이라 머지 뒤 P-12).

## 11. 작업 중인 브랜치 (감사 시점)

전달 노트는 공유 노트 폴더에 있다. 발견의 담당은 그 세션이다. 숫자는 그 브랜치에 남아 있는 것이다.

| 브랜치 | 감사 시점 | 높음 | 중간 | 낮음 | 전달 노트 | 가장 중요한 것 |
|---|---|---|---|---|---|---|
| `feature-US01-issue-21` | `678c082` (5커밋, 완료 보고) | 0 | 2 | 4 | `audit-to-issue-21.md` | `run_case` 가 예상 밖 예외에서 유료 실행을 끊음, `evaluation` 의 계층 규칙 |
| `feature-US01-issue-10-11-12` | `bb1a0e8` (18커밋, 완료 보고) | 1 | 2 | 6 | `audit-to-issue-10-11-12.md` | 키 형식 검증(A-01, 블록 입력으로 재현), 선언 크기가 없는 업로드, 임시 업로드 정리 |
| `feature-US06-issue-17` | `ea1d853` (6커밋) | 0 | 2 | 5 | `audit-to-issue-17.md` | 유료 실행 테스트의 스위치, `evaluation/__init__.py` 충돌 |
| `feature-US06-theme-series` | `e180864` (6커밋)에서 읽고 `805b9d1` (10커밋)까지 다시 확인 | 1 | 10 | 13 | `audit-to-theme-series.md` | 가려서 고친 글자가 다음 호출에서 유료로 다시 만들어짐, 음성 인식 키가 다른 주소로 감, 같은 입력의 유료 호출 반복, 녹음·편 문서의 덮어쓰기 |

감사하는 동안 브랜치가 움직였다.
- #10·#11·#12 세션은 처음 읽었을 때(`34e162f`)의 지적 가운데 셋을 `bb1a0e8` 까지에서 스스로 고쳤다 — ADR 번호를 0023·0024 로 옮김,
  업로드 오류의 413·415 매핑, 정체성 기준 이미지가 객체 삭제를 막던 것. 본문을 읽기 전의 크기 확인은 선언 크기(Content-Length)가 있는 요청만 막는다
  (없는 요청은 상한 10 MiB 를 넘겨 임시 파일에 18.9 MB 가 쓰였다, 재현). 임시 업로드 정리는 그 브랜치의 ADR-0024 에 한계로 적혀 후속 이슈 후보다.
- theme-series 는 감사 도중 유료 호출 어댑터(음성 인식 · VLM 테마 추출 · LLM 편 기획)와 유스케이스·조립을 더했다 (새 파일 64개, 약 7,500줄).
  아직 HTTP·CLI 로 노출되지 않았고 기본 설정은 모두 가짜 구현이라 지금 과금이 일어나지는 않는다. 노출하기 전에 볼 것이 노트의 1~11번이다.
- 전달 노트는 위 표의 커밋 기준으로 적었다. 노트마다 '공통 안내' 에 개념 단위 기준과, 그 브랜치의 새 모듈이 놓일 개념 자리를 적었다.

브랜치 사이의 충돌 (오케스트레이터가 조정할 것)
- ADR 번호: 0022 = #21, 0023·0024 = #10·#11·#12 (처음에는 0022 로 겹쳤고 그 세션이 옮겼다), 0025~0027 = theme-series
  (코드가 가리키지만 문서는 아직 그 브랜치에 없다), 0030·0031 = 감사. #17 은 아직 ADR 이 없다 (쓴다면 0028).
- `src/frame/evaluation/__init__.py` 를 #21 과 #17 이 붙어 있는 줄에서 고친다.
- `docs/ARCHITECTURE.md` 를 #21·#10·#17 이, `README.md` 를 셋이, `config/container.py` 를 #10 과 theme-series 가 고친다 (기계적 충돌).
- theme-series 가 `adapters/media_tools.py` 에 ffmpeg 실행기·영상 정보 읽기·SSIM 을 새로 썼다 (R-03 의 세 벌째). 감사 세션의 공용 실행기는
  그 모듈의 `run_ffmpeg(cmd, timeout_s, cwd=None)`·`tail(log, lines=3)` 와 같은 이름·인자로 만들어, 머지 뒤 import 만 바꾸면 되게 한다.
- theme-series 의 설정·조립(`config/theme_wiring.py`)은 `Settings`·레지스트리를 거치지 않는다. 병렬 작업 중 충돌을 피하려는 선택이었고,
  M-02 는 끝났다 (`5a98ae9`). 머지 뒤 그 등록을 `config/registrations/series.py` 로 옮기고 A-03(설정 읽기 통일)과 함께 합친다.
- 구조화 프롬프트·보정 규칙은 #17 의 골드셋 기록이 기대고 있어 그 브랜치가 dev 에 들어가기 전에는 고치지 않는다 (P-16).
- 네 브랜치 모두 `ruff check` 는 통과하고, 새로 쓴 파일은 포맷돼 있다. 계층 방향 위반도 없다.

## 12. 계획

### 12.1 지금 할 것 (3단계 — 다른 브랜치가 건드리지 않은 파일)

순서는 2단계(CI 게이트)가 먼저다 — 게이트가 선 뒤에 고쳐야 고친 것이 지켜진다. 그중 T-03(CI 에 ffmpeg)을 가장 먼저 한다.

규칙: 동작을 바꾸는 수정은 테스트를 먼저 쓰고 수정마다 따로 커밋한다. 구조 변경(옮기기·쪼개기)은 동작을 바꾸지 않고, 옛 경로에서 다시 내보낸다.
파일을 고치기 직전에 네 브랜치가 그 파일을 건드렸는지 다시 확인한다 (브랜치가 계속 움직인다).

**묶음 가 — 동작 수정 (테스트 먼저)**

| 순서 | 무엇 | 발견 | 고치는 파일 |
|---|---|---|---|
| 1 | 로컬 저장소: `..`·절대 경로 키 거부, 원자적 쓰기, `exists()` 계약 | A-01(저장소 쪽), A-09, A-21, T-05 | `adapters/storage/local_fs.py`, `memory.py`, 저장소 계약 테스트 |
| 2 | 유료 경로: 완료 기록의 기한, REPAIR 결과 키, 버려진 스레드의 제출, 편집 사전 확인, 일시 오류 분류, 미검증 분기 테스트 | A-32, A-33, A-34, A-36, A-37, T-04, T-06 | `adapters/generator/openrouter.py`, `decorators.py`, `application/ports/generator.py` |
| 3 | 검수: 예외 세 겹 방어, 일관성 집계, 부모 미판정 상속, 미판정 결과의 `detail`, 프레임 시각, LLM 재시도 상한 | A-13, A-22, A-23, A-16, A-28, A-24 | `application/checks.py`, `adapters/checker/*`, `adapters/llm/openai_client.py` |
| 4 | 도메인·구조화: 전파 결과 검증, DSL 의 `ANIMAL`·`#`·`@`, 형이 어긋난 응답의 재시도, 해석기 경계 | A-20, A-10, A-26(a·c·d), A-30 | `domain/services/revision_planning.py`, `adapters/structurer/{rule_based,llm_structurer}.py`, `adapters/change_request/*` |
| 5 | 키와 밖으로 나가는 글: 키 파일 파서, 키 표시 (키 다루기를 한 모듈로), 임시 경로, 전송 계층, live 테스트 단언 | A-25, A-29, A-38, T-08 | `adapters/llm/keys.py`, `adapters/http_transport.py`, `adapters/video_tools.py`, `tests/live/*` |
| 6 | 실행 환경: in-memory SQLite 엔진, 스레드 디스패처와 테스트, SQLite WAL, 뼈대 어댑터 거절 | A-41, T-02, A-07, A-14, A-31 | `adapters/persistence/sqlalchemy/engine.py`, `adapters/jobs/thread.py`, `adapters/tracker/opencv_stub.py`, `adapters/generator/stubs.py` |
| 7 | 운영: compose·Dockerfile, 런처의 기동 복구·`--lan`·가장자리, 번들 줄바꿈·키 위치 | O-03, O-04, O-05, O-08, O-09, O-11, O-12 | `docker/*`, `tools/demo.py`, `tools/bundle.py` |

**묶음 나 — 구조 (동작 그대로)**

| 순서 | 무엇 | 발견 | 비고 |
|---|---|---|---|
| 8 | 배치 규칙 ADR-0031 · 용어집 · 모듈 지도(`docs/MODULE_MAP.md`). 어댑터 공용 모듈을 `adapters/shared/` 로 (영상 처리 · VLM 질의 · API 키 · HTTP), 조립기를 `adapters/assembler/` 로, 패키지 설명과 `__all__` | L-03, R-03, K-01, K-02, K-03, K-04, K-05 | 옛 경로(`adapters/common.py` 등) 재노출. 실행기는 theme-series 의 `run_ffmpeg`·`tail` 과 같은 이름·인자 |
| 9 | VLM 검수기 공통 골격과 `FrameSampler` | M-04, R-10, A-27, T-10 | `checker/vlm_*.py` 는 얇은 재노출 모듈로 |
| 10 | `openrouter.py` → `openrouter/` 하위 패키지 (제출 · 대기 · 원장 · 부모 클립 편집 · 미리보기 …), 설정 묶음 객체 | M-01, C-06, A-39, A-40 | `openrouter/__init__.py` 가 공개 이름 재노출. 안쪽 모듈은 `_` 접두사 |
| 11 | `domain/revision/` 패키지 (수정 의도 · 속성 변경 · 전파 · 요청 풀기), `VideoVersion` 필드 순서, 수정 가능 확인, `Clock` 필수화, `ClipProduction` 값 객체(새 모듈 + `plan_revision.py`) | M-07, K-05, C-03, C-04, R-05, R-06, R-04 | `domain/services/propagation.py`·`revision_planning.py` 는 재노출 모듈로 |
| 12 | `tools/demo.py`·`bundle.py` 분해, 포맷 6파일 | M-05, C-01 | `python -m frame.tools.demo` 진입 유지 |
| 13 | `GenerateStage` 의 정체성 기준 뽑기를 협력 객체로 | M-06 | 다른 브랜치가 `stages.py` 를 건드리면 머지 뒤로 |
| 14 | 테스트 지원 코드 모으기, 큰 테스트 파일 나누기 | T-11 | 10·9 번과 같은 경계로 |
| 15 | ADR 보완 줄, 낡은 코드 주석, 쓰이지 않는 코드 | D-07, D-08, C-10 | — |

`config/registry.py` 분해(M-02)는 §12.3 결정 3(지금 분해)에 따라 묶음 가 1 다음에 했다 (`5a98ae9`).

**진행 (2026-11-14)** — 발견마다의 상태와 커밋은 각 항목에 있다.

| 묶음 | 상태 | 커밋 | 해결한 발견 |
|---|---|---|---|
| 가 1 저장소 | 끝 | `11e06e2` · `f8cf87a` · `a2be59c` | A-01(저장소 쪽), A-21, A-09(a·c), T-05(일부) |
| 등록 모듈 분해 | 끝 | `3c5bd85` · `5a98ae9` | M-02 |
| 가 2 유료 경로 | 끝 | `fcd03cd` · `151de2b` · `7265865` · `63428d0` · `7d4a140` · `0eb1214` · `d7a19a1` · `4d56bbc` · `4eb7bf5` · `3a6096b` | A-32, A-33, A-34, A-36, A-37, A-09(b), T-04, T-06, R-02 |

### 12.2 머지 뒤로 미루는 것

| ID | 무엇 | 기다리는 브랜치 | 발견 |
|---|---|---|---|
| P-01 | 도메인 키 형식 규칙 (#10 세션이 하지 않았을 때) | #10·#11·#12 | A-01 |
| P-02 | LLM 키 저장소 공유 조건 | #10·#11·#12 (`container.py`) | A-02 |
| P-03 | 환경 변수 읽기 통일·범위 검사 | #10·#11·#12 (`settings.py`), theme-series | A-03 |
| P-04 | `mappers.py`·`edit_runner.py` 를 `ClipProduction` 으로 | #10·#11·#12, #21 | R-04 |
| P-05 | 구조화 유스케이스의 저장 단위·실패 정규화 | #10·#11·#12 | A-05, R-09 |
| P-06 | `evaluation/` 을 `edits/`·`structuring/` 하위 패키지로 | #21, #17 | — |
| P-07 | 사각형 타입을 `Region` 하나로 | theme-series | R-07 |
| P-08 | 장면 조건 해시 내용·타임라인 검사를 도메인 함수로, 해시·키 이름 맞추기 | #10·#11·#12 (`ir.py`), theme-series | R-01, R-08, K-01 |
| P-09 | presentation 의 스키마·매퍼·라우터 분해, 요청 → 명령 변환 | #10·#11·#12 | M-03, R-05 |
| P-10 | 요청 제한기 기록 정리 | #10·#11·#12 | A-08 |
| P-11 | 문서: `ARCHITECTURE.md` §3.1 표·§4 트리·§8 API 표·§12 ADR 목록, README, `.env` 안내와 `.env.example`. `ARCHITECTURE.md` §5 절차 3·§6 패턴 표와 `TDD_WORKFLOW.md` §4·§5 의 등록 자리를 `config/registrations/<포트>.py` 로 | #21·#10·#17 (문서를 고친 브랜치 모두) | L-02, D-01, D-02, D-03, D-04, D-08, M-02 |
| P-12 | `presentation/app.py`·`schemas.py` 포맷 (#10 세션이 하지 않았을 때), `__all__`·오류 클래스 위치, `dto.py`·`queries.py` 를 자원별로, 나머지 열거형을 개념 모듈 옆으로, 쓰이지 않는 전이의 주석 | #10·#11·#12 | C-01, C-07, C-09, C-10, K-05 |
| P-13 | Repository·UnitOfWork 계약 테스트 (commit·rollback·예외 종류), `save` 의 계약 | #10·#11·#12 | T-05, A-12 |
| P-14 | OpenAPI 계약 서명 강화, 오류 응답 명세, CORS `DELETE`·저장소 URI 직접 지정 막기 (#10 세션이 하지 않았을 때) | #10·#11·#12 | T-09, D-05, A-04, A-19 |
| P-15 | `adapters/media_tools.py`·`fake_media.py` 를 `adapters/shared/media/` 위로, 테마 도메인을 하위 패키지로 (그 세션이 하지 않았을 때) | theme-series | R-03, L-03, K-03 |
| P-16 | 구조화 프롬프트·보정 규칙을 바꾸는 수정 (프롬프트의 참조 이미지 URI, 빈 문자열 키 보정). 골드셋 리플레이로 점수 변화를 보고 프롬프트 버전을 올린다 | #17 | A-26(b), A-29 |

옛 경로에서 다시 내보내는 호환 import 는 모든 작업 중 브랜치가 dev 에 들어간 뒤 한 번에 걷어낸다 (그때 `git grep` 으로 쓰는 곳이 없는지 확인).

### 12.3 결정이 필요한 것

1. **개발 도구 설치** — **결정됨 (2026-11-14 사용자 승인, 설치함).** 로컬 공유 `.venv` 에 `mypy 2.4.1` 과 그것이 요구하는 패키지 넷
   (`ast_serialize 0.13.1` · `librt 0.16.0` · `mypy_extensions 1.2.0` · `pathspec 1.1.1`)을 설치했다. 내려받은 크기는 합쳐 약 12.7MB 이고,
   이미 있던 패키지는 바뀌지 않았다 (`pip check` 통과). `pyproject.toml` dev extra 에 이미 있던 도구이고 CI 는 이미 설치한다.
   계층 규칙은 pytest 테스트로, 보안 정적 분석은 ruff 의 `S` 규칙으로, 복잡도는 ruff 의 `C90` 으로 하므로
   import-linter·bandit·radon 은 설치하지 않는다. `pip-audit` 은 CI 잡 안에서만 설치한다 (로컬 설치 없음).
2. **ADR 번호** — 감사 세션은 0030(CI 품질 게이트와 기준선 방식)·0031(개념 단위 배치 규칙·용어집·호환 import)을 쓴다.
   머지 순서가 정해지면 빈 번호(0028·0029)를 당길지 정한다.
3. **공용 파일 리팩터링의 순서** — **결정됨 (2026-11-14 사용자: 지금 분해. `5a98ae9`).** `config/registry.py` 분해(M-02)를 지금 할지. 감사 시점에 네 브랜치 모두 이 파일을 건드리지 않았다
   (theme-series 는 `config/theme_wiring.py` 를 따로 두고 `container.py` 만 고친다). 지금 하면 뒤에 등록을 더하는 브랜치가 새 구조를 따르면 된다.
4. **지표·정책** — A-06(리스 하트비트, 스키마 변경), A-17·A-18(보존율 정의, 연구 문서 §7 결정 2), A-22(일관성 검수에서 판정된 프레임이
   표본의 몇 % 에 못 미치면 미판정으로 볼지 — 제안 50%), A-35(편집 거절 뒤 진행 중인 같은 작업을 이어 받을지, 지금처럼 새로 제출할지).
5. **GitHub 저장소 설정** (제안만) — `dev` 에 브랜치 보호: 필수 검사 `test`, `frontend`, `demo-smoke (ubuntu-latest)`. 머지 전 최신 상태 요구.
   기본 브랜치를 `dev` 로 (지금 `main` 에는 워크플로 파일이 없어 수동 실행을 쓸 수 없다, O-13). 태그 보호 규칙 (O-01).
6. **로컬 Python** — 공유 `.venv` 를 3.11 정식판으로 다시 만들지 (C-08). 지금의 3.11.0rc2 에는 tarfile 안전 필터가 없어 번들 테스트 2개가 건너뛰어진다.
7. **잠금 파일** — 의존성의 정확한 버전을 잠금 파일로 고정해 CI·Docker·번들이 같은 조합을 쓰게 할지 (O-01·O-02).
   만들려면 `pip-tools` 나 `uv` 같은 도구가 필요하다 (설치 승인 대상). 하지 않으면 개발 도구 버전만 pyproject 에 고정한다.
