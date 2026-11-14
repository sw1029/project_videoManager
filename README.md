# project_videoManager · FRAME — 구조화된 중간 표현 기반 숏폼 비디오 선택적 편집·일관성 제어 프레임워크 (백엔드·파이프라인)

자연어 요구사항을 장면·객체·속성·관계·시간 조건의 **중간표현(IR)** 으로 구조화하고, 그 조건으로 영상을 생성·추적·검수하며,
선택한 객체의 속성만 바꾼 **수정 버전** 을 원본을 보존한 채 만들어 전후를 비교하는 백엔드다.

- 아키텍처 제안서: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- TDD 워크플로: [docs/TDD_WORKFLOW.md](docs/TDD_WORKFLOW.md)
- 결정 기록: [docs/adr](docs/adr)

## 새 기기에서 시연 (Windows · macOS · Linux)

화면과 API 를 서버 하나로 띄우고 입력 프리셋까지 채워 주는 런처가 있다. 자세한 내용은 [docs/DEMO.md](docs/DEMO.md).

**설치 없이**: OS별 번들(`frame-demo-<대상>.zip|tar.gz`, 휴대용 Python·화면 동봉, 약 30MB)을 Releases 에서 받아
압축을 풀고 `run.cmd`(Windows) 또는 `sh run.sh`(macOS·Linux)를 실행한다. 번들은 `v*` 태그를 올리면 CI 가 만든다.

**저장소에서** (Python 3.11+, Node.js 18+ 필요):

| OS | 설치 (한 번) | 실행 |
|---|---|---|
| Windows | `scripts\setup.cmd` | `scripts\run.cmd` |
| macOS · Linux | `sh scripts/setup.sh` | `sh scripts/run.sh` |

`run --offline` 은 키·인터넷 없이 DSL 프리셋으로, `run --lan` 은 같은 네트워크의 iPhone·iPad 에서 보기용으로 띄운다.
OpenAI 키는 설치 때 입력하거나(`.demo/openai.key`, git 제외) 실행 후 화면의 키 칸에 넣는다(서버 메모리에만, 같은 기기에서만).

## 빠른 시작 (개발)

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"        # Linux/macOS 는 .venv/bin/pip
.venv/Scripts/python -m pytest                # 전체 테스트 (Fake 어댑터 + SQLite)
.venv/Scripts/python -m uvicorn --factory frame.presentation.main:create_default_app --reload
```

기본 설정은 메모리 저장소 + Fake 모델 + 요청 스레드 내 즉시 실행이라 설치 직후 바로 `http://localhost:8000/docs` 에서 흐름을 시연할 수 있다.

### 시연 흐름 (규칙 기반 구조화 DSL)

```bash
curl -X POST localhost:8000/api/projects -H "Content-Type: application/json" -d '{
  "title": "카페 신제품 숏폼",
  "prompt": "scene intro 0-4000 : 바리스타가 컵을 들어 보인다\nscene closeup 4000-8000 : 컵 클로즈업\nentity barista PERSON\nentity cup PROP\nin intro: barista, cup\nin closeup: cup\ncup.color = red\nbarista holds -> cup @ intro"
}'
```

1. 응답의 `version_id` 로 `GET /api/versions/{id}` → `DRAFT` 와 IR 확인
2. `POST /api/versions/{id}/generations` → `202 QUEUED` → 잠시 후 `COMPLETED`
3. `GET /api/versions/{id}/checks`, `GET /api/versions/{id}/entities/cup/tracks`
4. `POST /api/versions/{id}/revisions` `{"entity_key":"cup","predicate":"color","new_value":"teal"}` → 새 DRAFT → 2~3 반복
5. `GET /api/versions/{새 id}/comparison` → 부모 대비 장면별 재사용(`REUSED`)·재생성, 바뀐 요구조건과 판정, 검수 지표 차이
6. `GET /api/projects/{project_id}/dashboard` → 버전 트리, 버전별 상태·검수 요약·바뀐 장면 수, 상태별 개수·최신 버전

## 구성 (환경 변수 `FRAME_*`, `.env.example` 참고)

| 키 | 값 | 의미 |
|---|---|---|
| `FRAME_STRUCTURER` | `rule_based`(DSL 입력) · `llm`(자연어 입력) · `fake` | 요구사항 구조화 모델 |
| `FRAME_GENERATOR` | `fake` · `external_api`(OpenRouter) · `diffusers`(뼈대) | 영상 생성 모델 (+`FRAME_GENERATOR_RETRIES`, `FRAME_GENERATOR_CACHE`: 기본 켜짐, 같은 조건·설정의 장면은 재시작 뒤에도 한 번만 생성) |
| `FRAME_VIDEO_MODEL` / `FRAME_VIDEO_RESOLUTION` | OpenRouter 영상 모델 id (기본 `alibaba/wan-3.0`) / `480p` · `720p`(기본) · `1080p` | `external_api` 의 모델과 해상도 (화면비 `FRAME_VIDEO_ASPECT_RATIO`=`9:16`, 클립 fps `FRAME_VIDEO_FPS`=30) |
| `FRAME_VIDEO_KEY_FILE` | 키 파일 경로 (또는 환경 변수 `OPENROUTER_API_KEY`) | OpenRouter 키 (`sk-or-...`). 저장소에 넣지 않는다 |
| `FRAME_VIDEO_FFMPEG` | 실행 파일 경로 (비우면 PATH → `video` extra 의 imageio-ffmpeg) | 클립 정규화·조립용 ffmpeg |
| `FRAME_ASSEMBLER` | `fake` · `ffmpeg` | 최종 영상 조립 (`ffmpeg` 은 장면 클립을 이어 mp4 로. 실제 영상 생성기와 함께 쓴다) |
| `FRAME_TRACKER` | `fake` · `opencv` | 객체 추적 모델 |
| `FRAME_CHECKERS` | 쉼표 목록 (`fake_requirement` · `fake_consistency` · `fake_preservation` · `vlm_requirement` · `vlm_consistency` · `vlm_preservation` · `ssim_preservation`) | 검수기 (요구사항·일관성·보존). `vlm_consistency` 는 표본 프레임을 VLM 이 판정해 고정 속성 유지율을 재고, `ssim_preservation` 은 다시 만든 장면을 부모 클립과 SSIM 으로 비교한다. `vlm_requirement` 는 요구조건마다 반영 여부를 판정하고(지표 2), `vlm_preservation` 은 다시 만든 장면에서 유지할 요소가 그대로인지 부모 클립과 견준다 ([ADR-0018](docs/adr/0018-requirement-and-keep-checkers.md)) (ffmpeg 필요, [ADR-0015](docs/adr/0015-identity-references-and-consistency-check.md)·[0016](docs/adr/0016-parent-clip-editing.md)) |
| `FRAME_IDENTITY` | `off`(기본) · `vlm_crop` · `frame` · `fake` | 장면 사이 객체 일관성: 객체가 나온 클립에서 정체성 기준 이미지를 뽑아 그 객체가 나오는 다른 장면 생성에 참조로 넘긴다. `vlm_crop` 은 VLM 으로 객체를 잘라 내고 `frame` 은 화면 전체를 쓴다 (+`FRAME_IDENTITY_KINDS`=`PERSON,ANIMAL,PROP,OTHER`, `FRAME_IDENTITY_MAX_REFERENCES`=3, `FRAME_IDENTITY_VERIFY_ATTRIBUTES`=true: 요구한 고정 속성과 다르게 나온 객체는 기준으로 쓰지 않음). 조사·실험 기록은 [docs/research/character-consistency.md](docs/research/character-consistency.md) |
| `FRAME_VLM_MODEL` | 모델 이름 (비우면 `FRAME_LLM_MODEL`) | 이미지를 읽는 모델. `vlm_crop`·`vlm_consistency` 가 쓴다. 공급자·키·주소는 `FRAME_LLM_*` 와 같다 |
| `FRAME_VIDEO_EDIT_MODE` / `FRAME_VIDEO_EDIT_MODEL` | `off`(기본) · `previous_job` · `video_reference` / 편집 모델 id | 수정 버전에서 다시 만드는 장면을 부모 클립 편집으로 만든다. 편집 요청이 거절되면 새로 생성한다. 실호출로 확인한 조합은 `video_reference` + `black-forest-labs/flux-video-edit` 이고, 부모 클립을 만든 작업의 저장본이 남아 있는 약 24시간 안에만 된다 ([ADR-0016](docs/adr/0016-parent-clip-editing.md)) |
| `FRAME_VIDEO_EDIT_PARENT_WAIT_S` | `10`(기본, 초) | `video_reference` 편집에서 부모 작업의 저장본 주소가 상태 응답에 없을 때 다시 묻는 시간. 넘기면 새로 생성한다. 방금 만든 영상은 주소가 몇 분 늦게 나오므로, 생성 직후의 수정도 편집으로 만들려면 늘린다 (그만큼 기다린다). 변경 계획(`POST /versions/{id}/revision-plans`)을 먼저 요청하면 편집이 될지 미리 알려 주고, 그때 받은 주소를 메모리에 30분 들고 있다가 쓴다. [ADR-0016](docs/adr/0016-parent-clip-editing.md) · [ADR-0020](docs/adr/0020-production-preview-and-held-parent-url.md) |
| `FRAME_CHANGE_INTERPRETER` | `auto`(기본) · `rule_based` · `llm` | 글로 쓴 변경 요청(`POST /versions/{id}/revision-plans` 의 `request_text`)을 구조화하는 해석기. `auto` 는 구조화기를 따른다 (`FRAME_STRUCTURER=llm` 이면 `llm`). `rule_based` 는 `cup.color = teal @ 장면; keep: barista.action` 형식만 읽는다 ([ADR-0019](docs/adr/0019-revision-plan-and-change-request.md)). 수정의 종류(`CHANGE`·`REPAIR`)와 유지할 요소는 [ADR-0017](docs/adr/0017-revision-intent-repair-and-keep.md), 컨셉 화면 관점의 검토는 [docs/research/concept-ux-review.md](docs/research/concept-ux-review.md) |
| `FRAME_PERSISTENCE` / `FRAME_DB_URL` | `memory` · `sqlalchemy` / MySQL·SQLite URL | 저장소 |
| `FRAME_ARTIFACT_STORE` / `FRAME_ARTIFACT_DIR` | `memory` · `local_fs` | 산출물 저장 |
| `FRAME_JOBS` | `inline` · `thread` · `recording` | 비동기 실행 |
| `FRAME_GENERATOR_TIMEOUT_S` | 초 (0·빈 값 = 끔, 기본 끔) | 장면 하나 생성의 범용 시간 제한. 넘으면 `GENERATION_TIMEOUT` 으로 실패하고 재시도하지 않는다. `external_api` 는 `FRAME_VIDEO_TIMEOUT_S` + 300초 이상 ([ADR-0012](docs/adr/0012-generation-timeout-partial-results-progress.md)) |
| `FRAME_WORKER_LEASE_S` / `FRAME_WORKER_MAX_ATTEMPTS` / `FRAME_RECOVERY_INTERVAL_S` | 초(1800) / 횟수(3) / 초(60, 0 = 기동 때만) | 재시작 복구 ([ADR-0013](docs/adr/0013-restart-recovery-and-persistent-generation-cache.md)): 기동 때 QUEUED·STRUCTURING 은 다시 실행하고 PROCESSING 은 재시도, 실행 중에는 리스를 넘긴 PROCESSING 을 재시도. 시도 상한이면 `FAILED(WORKER_LOST)` |
| `FRAME_CORS_ORIGINS` | 쉼표 목록 | 다른 출처 화면 허용 (기본 꺼짐) |
| `FRAME_FRONTEND_DIST` | 폴더 경로 | 빌드된 화면을 같은 출처 `/` 에서 서빙 |
| `FRAME_RATE_LIMIT_PER_MINUTE` | 정수 | 공개 데모용 IP 별 변경 요청(POST·PUT) 제한 (0 = 꺼짐) |
| `FRAME_REFERENCE_IMAGE_MAX_BYTES` | 정수 (기본 10485760 = 10 MiB) | 참조 이미지 업로드(`POST /api/reference-images`)의 크기 상한. PNG·JPEG·WebP 만 받는다 ([ADR-0023](docs/adr/0023-reference-image-upload.md)) |
| `FRAME_LLM_RUNTIME_KEY` | `true` · `false`(기본) | 화면에서 OpenAI 키 입력 허용 (키 없이 기동, 메모리에만 보관, 같은 기기에서만) |

Docker (API + MySQL 8.4): `docker compose -f docker/docker-compose.yml up --build`

## 프론트엔드 연동

API 계약 전문은 [docs/openapi.json](docs/openapi.json) 이다. API 를 바꾸면 `python -m frame.tools.export_openapi` 로 갱신해야
테스트가 통과하며, PR 에 변경을 적어 프론트 담당자가 알 수 있게 한다.

- **오류 형식**: 모든 오류는 `{error_code, error_message}` 이고, 요청 형식 오류(422)만 `details[{loc, message}]` 를 더한다.
- **상태 갱신**: 구조화·생성은 비동기다. 화면은 상태가 STRUCTURING·QUEUED·PROCESSING 인 동안 `GET /api/versions/{id}` 를 주기적으로 조회한다.
- **영상**: `video_url`, `clip_url` 을 `<video src>` 에 그대로 쓴다. `*_media_type` 이 `video/` 가 아니면 Fake 매니페스트(JSON)이므로 대체 화면을 보여준다.
- **전후 비교**: `GET /api/versions/{id}/comparison` (`?against=` 생략 시 부모) 한 번으로 양쪽 영상, 장면별 `status`
  (`REUSED`·`REGENERATED`·`ADDED`·`REMOVED`·`UNAVAILABLE`=클립 없음)와 양쪽 `clip_url`, 요구조건별 변경·판정, `checks_delta` 를 받는다 ([ADR-0014](docs/adr/0014-comparison-and-dashboard-api.md)).
- **대시보드**: `GET /api/projects/{id}/dashboard` 가 버전 트리(`parent_version_id`·`child_version_ids`·`depth`)와 버전별 상태·오류·영상·검수 요약·
  `changed_scene_count`, 상태별 개수·최신 버전을 한 번에 준다.
- **입력 예시**: `/health` 의 `structuring_input` 이 `dsl` 이면 DSL 예시를, `natural_language` 이면 자연어 예시를 보여준다.
- **장면 수준 조건 수정**: `POST /revisions` 에서 `entity_key` 를 생략하면 장소·조명·분위기 같은 장면 조건을 바꾼다.
- **블록 입력 (IR 직접 입력)**: `POST /api/projects` 에 `prompt` 대신 `ir`(GET 의 `ir` 과 같은 모양)을 보내면 LLM 없이 보정·검증만 거쳐
  바로 DRAFT 가 된다 (201, `repair_notes` 에 서버가 고친 것). 객체의 화면 라벨은 `display_name` 에 둔다 (생성 조건이 아니다).
- **구조화 결과 수정**: DRAFT 에서 `PUT /api/versions/{id}/ir` 로 고친 IR 을 보내면 보정·검증 뒤 저장하고 고친 버전과
  `repair_notes` 를 돌려준다. 생성이 접수된 뒤에는 409 `VERSION_NOT_EDITABLE` ([ADR-0022](docs/adr/0022-direct-ir-input-draft-edit-and-display-name.md)).
- **참조 이미지 업로드**: `POST /api/reference-images` 에 multipart `file`(PNG·JPEG·WebP)을 올리면 `image_uri` 를 돌려준다.
  그 값을 `POST /api/projects` 의 `reference_images[].image_uri` 에 쓰면 서버가 `refs/{project_id}/` 로 옮겨 버전의 참조 이미지로 남기고,
  `reference_images[].image_url` 로 내려받을 수 있다 ([ADR-0023](docs/adr/0023-reference-image-upload.md)).

화면 개발용 백엔드 실행 예시 (데이터 유지 + 비동기 실행 + 영상 Range 지원):

```bash
FRAME_JOBS=thread FRAME_PERSISTENCE=sqlalchemy FRAME_DB_URL=sqlite+pysqlite:///./frame.db FRAME_ARTIFACT_STORE=local_fs python -m uvicorn --factory frame.presentation.main:create_default_app --reload
```

배포는 화면을 빌드한 뒤 `FRAME_FRONTEND_DIST=./frontend/dist` 로 같은 출처에서 서빙하는 방식을 권장한다. 화면을 다른 출처에 두면
`FRAME_CORS_ORIGINS` 를 설정한다. 공개 데모에서는 `FRAME_RATE_LIMIT_PER_MINUTE` 로 요청 수를 제한한다.

## 저장소 구조

```
src/frame/{domain,application,adapters,presentation,config,evaluation}
tests/{unit,contract,integration,api,acceptance}
docs/{ARCHITECTURE.md,TDD_WORKFLOW.md,adr/}
```

브랜치 전략은 문서대로 `main` / `feature-{US번호}`, PR 에 US 번호·변경·검증 결과를 적는다.

## LLM 구조화 호출 테스트

`FRAME_STRUCTURER=llm` 이면 자연어 브리프를 OpenAI(또는 OpenAI 호환 로컬 서버)로 구조화한다. API 키는 환경 변수
`OPENAI_API_KEY` 나 키 파일(`FRAME_LLM_KEY_FILE`)로 전달하고 저장소에는 넣지 않는다. 키나 `llm` 추가 패키지가 없으면
서버가 기동 단계에서 실패한다. 시연용으로 `FRAME_LLM_RUNTIME_KEY=true` 를 켜면 키 없이 기동하고, 같은 기기에서 연 화면의
키 칸(`PUT /api/settings/llm-key`)으로 넣은 키를 메모리에만 둔다 ([ADR-0010](docs/adr/0010-demo-runtime-key-and-launcher.md)).

```bash
pip install -e ".[dev,llm]"
```

```bash
FRAME_STRUCTURER=llm FRAME_LLM_KEY_FILE=F:/key.txt python -m frame.tools.structure_cli --duration-ms 12000 --brief "카페 신제품 숏폼. 장면 1 (4초): 바리스타가 빨간 머그컵을 들어 보인다. 장면 2 (4초): 컵 클로즈업. 장면 3 (4초): 바리스타가 손을 흔든다."
```

```bash
FRAME_STRUCTURER=llm FRAME_LLM_KEY_FILE=F:/key.txt python -m pytest tests/live -m slow -s
```

로컬 3090 에서 vLLM 등 OpenAI 호환 서버를 띄웠다면 같은 명령에 `FRAME_LLM_BASE_URL=http://localhost:8001/v1`,
`FRAME_LLM_MODEL=<서빙 모델명>`, `FRAME_LLM_STRICT_SCHEMA=false` 를 더한다. 모델 호출은 `LLMClient` 포트 뒤에 있어
유스케이스·API·테스트는 바뀌지 않는다.

## 영상 생성 (OpenRouter) 호출 테스트

`FRAME_GENERATOR=external_api` 이면 장면마다 OpenRouter 영상 API(`POST /api/v1/videos` → 폴링 → `/content` 다운로드)로
클립을 만들고, ffmpeg 로 장면 길이·fps·해상도에 맞춘 무음 mp4 로 바꿔 저장한다. `FRAME_ASSEMBLER=ffmpeg` 면 장면 클립을 이어 최종 mp4 를 만든다 ([ADR-0011](docs/adr/0011-openrouter-video-generator.md)).
모델은 `FRAME_VIDEO_MODEL` 하나로 바꾸고(기본 Wan 3.0), 허용 길이·화면비·seed 지원은 키 없이 받는 모델 메타데이터를 따른다.
키는 `OPENROUTER_API_KEY` 나 키 파일(`FRAME_VIDEO_KEY_FILE`, `sk-or-...`)로만 전달한다. 키나 ffmpeg 가 없으면 서버가 기동 단계에서 실패한다.

```bash
pip install -e ".[dev,video]"
```

```bash
FRAME_GENERATOR=external_api FRAME_VIDEO_KEY_FILE=<키 파일> FRAME_VIDEO_RESOLUTION=480p python -m pytest tests/live/test_openrouter_live.py -m slow -s
```

카페 예시의 4초 장면 하나를 만들고(Wan 3.0 480p 약 $0.20, 720p 약 $0.40) 비용(`usage.cost`)·소요 시간·출력 규격을 출력한다.
결과는 `./artifacts/clips/live-<시각>/intro.mp4` 에 남는다. 접수된 작업이 실패하거나 시간을 넘기면 과금 여부를 알 수 없어
다시 제출하지 않는다 (`FRAME_GENERATOR_RETRIES` 도 건너뜀). 같은 조건·같은 설정의 장면은 생성 캐시(`FRAME_GENERATOR_CACHE`, 기본 켜짐)와
작업 원장으로 재시작 뒤에도 다시 제출하지 않는다 ([ADR-0013](docs/adr/0013-restart-recovery-and-persistent-generation-cache.md)).
이 테스트를 같은 조건으로 24시간 안에 다시 돌리면 원장이 이전 결과를 다시 쓰고 실패하므로, 실제 호출을 다시 보려면 `./artifacts/ledger/openrouter` 를 지운다.
