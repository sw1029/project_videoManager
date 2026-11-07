# project_videoManager · FRAME — 구조화된 중간 표현 기반 숏폼 비디오 선택적 편집·일관성 제어 프레임워크 (백엔드·파이프라인)

자연어 요구사항을 장면·객체·속성·관계·시간 조건의 **중간표현(IR)** 으로 구조화하고, 그 조건으로 영상을 생성·추적·검수하며,
선택한 객체의 속성만 바꾼 **수정 버전** 을 원본을 보존한 채 만들어 전후를 비교하는 백엔드다.

- 아키텍처 제안서: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- TDD 워크플로: [docs/TDD_WORKFLOW.md](docs/TDD_WORKFLOW.md)
- 결정 기록: [docs/adr](docs/adr)

## 빠른 시작

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
| `FRAME_STRUCTURER` | `rule_based` · `fake` · `llm` | 요구사항 구조화 모델 |
| `FRAME_GENERATOR` | `fake` · `diffusers` · `external_api` | 영상 생성 모델 (+`FRAME_GENERATOR_RETRIES`, `FRAME_GENERATOR_CACHE`) |
| `FRAME_TRACKER` | `fake` · `opencv` | 객체 추적 모델 |
| `FRAME_CHECKERS` | 쉼표 목록 | 검수기 (요구사항·일관성·보존) |
| `FRAME_PERSISTENCE` / `FRAME_DB_URL` | `memory` · `sqlalchemy` / MySQL·SQLite URL | 저장소 |
| `FRAME_ARTIFACT_STORE` / `FRAME_ARTIFACT_DIR` | `memory` · `local_fs` | 산출물 저장 |
| `FRAME_JOBS` | `inline` · `thread` · `recording` | 비동기 실행 |

Docker (API + MySQL 8.4): `docker compose -f docker/docker-compose.yml up --build`

## 저장소 구조

```
src/frame/{domain,application,adapters,presentation,config,evaluation}
tests/{unit,contract,integration,api,acceptance}
docs/{ARCHITECTURE.md,TDD_WORKFLOW.md,adr/}
```

브랜치 전략은 문서대로 `main` / `feature-{US번호}`, PR 에 US 번호·변경·검증 결과를 적는다.

## LLM 구조화 호출 테스트

`FRAME_STRUCTURER=llm` 이면 자연어 브리프를 OpenAI(또는 OpenAI 호환 로컬 서버)로 구조화한다. API 키는 환경 변수
`OPENAI_API_KEY` 나 키 파일(`FRAME_LLM_KEY_FILE`)로만 전달하고 저장소에는 넣지 않는다.

```bash
pip install -e ".[dev,llm]"
```

```bash
FRAME_STRUCTURER=llm FRAME_LLM_PROVIDER=openai FRAME_LLM_KEY_FILE=F:/key.txt python -m frame.tools.structure_cli --duration-ms 12000 --brief "카페 신제품 숏폼. 장면 1 (4초): 바리스타가 빨간 머그컵을 들어 보인다. 장면 2 (4초): 컵 클로즈업. 장면 3 (4초): 바리스타가 손을 흔든다."
```

```bash
FRAME_STRUCTURER=llm FRAME_LLM_PROVIDER=openai FRAME_LLM_KEY_FILE=F:/key.txt python -m pytest tests/live -m slow -s
```

로컬 3090 에서 vLLM 등 OpenAI 호환 서버를 띄웠다면 같은 명령에 `FRAME_LLM_BASE_URL=http://localhost:8001/v1`,
`FRAME_LLM_MODEL=<서빙 모델명>`, `FRAME_LLM_STRICT_SCHEMA=false` 를 더한다. 모델 호출은 `LLMClient` 포트 뒤에 있어
유스케이스·API·테스트는 바뀌지 않는다.
