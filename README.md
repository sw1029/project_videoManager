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
4. `POST /api/versions/{id}/revisions` `{"entity_key":"cup","predicate":"color","new_value":"teal"}` → 새 DRAFT → 2~3 반복 → 두 버전 비교

## 구성 (환경 변수 `FRAME_*`, `.env.example` 참고)

| 키 | 값 | 의미 |
|---|---|---|
| `FRAME_STRUCTURER` | `rule_based`(DSL 입력) · `llm`(자연어 입력) · `fake` | 요구사항 구조화 모델 |
| `FRAME_GENERATOR` | `fake` · `diffusers` · `external_api` | 영상 생성 모델 (+`FRAME_GENERATOR_RETRIES`, `FRAME_GENERATOR_CACHE`) |
| `FRAME_TRACKER` | `fake` · `opencv` | 객체 추적 모델 |
| `FRAME_CHECKERS` | 쉼표 목록 | 검수기 (요구사항·일관성·보존) |
| `FRAME_PERSISTENCE` / `FRAME_DB_URL` | `memory` · `sqlalchemy` / MySQL·SQLite URL | 저장소 |
| `FRAME_ARTIFACT_STORE` / `FRAME_ARTIFACT_DIR` | `memory` · `local_fs` | 산출물 저장 |
| `FRAME_JOBS` | `inline` · `thread` · `recording` | 비동기 실행 |
| `FRAME_CORS_ORIGINS` | 쉼표 목록 | 다른 출처 화면 허용 (기본 꺼짐) |
| `FRAME_FRONTEND_DIST` | 폴더 경로 | 빌드된 화면을 같은 출처 `/` 에서 서빙 |
| `FRAME_RATE_LIMIT_PER_MINUTE` | 정수 | 공개 데모용 IP 별 POST 요청 제한 (0 = 꺼짐) |
| `FRAME_LLM_RUNTIME_KEY` | `true` · `false`(기본) | 화면에서 OpenAI 키 입력 허용 (키 없이 기동, 메모리에만 보관, 같은 기기에서만) |

Docker (API + MySQL 8.4): `docker compose -f docker/docker-compose.yml up --build`

## 프론트엔드 연동

API 계약 전문은 [docs/openapi.json](docs/openapi.json) 이다. API 를 바꾸면 `python -m frame.tools.export_openapi` 로 갱신해야
테스트가 통과하며, PR 에 변경을 적어 프론트 담당자가 알 수 있게 한다.

- **오류 형식**: 모든 오류는 `{error_code, error_message}` 이고, 요청 형식 오류(422)만 `details[{loc, message}]` 를 더한다.
- **상태 갱신**: 구조화·생성은 비동기다. 화면은 상태가 STRUCTURING·QUEUED·PROCESSING 인 동안 `GET /api/versions/{id}` 를 주기적으로 조회한다.
- **영상**: `video_url`, `clip_url` 을 `<video src>` 에 그대로 쓴다. `*_media_type` 이 `video/` 가 아니면 Fake 매니페스트(JSON)이므로 대체 화면을 보여준다.
- **입력 예시**: `/health` 의 `structuring_input` 이 `dsl` 이면 DSL 예시를, `natural_language` 이면 자연어 예시를 보여준다.
- **장면 수준 조건 수정**: `POST /revisions` 에서 `entity_key` 를 생략하면 장소·조명·분위기 같은 장면 조건을 바꾼다.

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
