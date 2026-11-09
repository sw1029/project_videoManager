# FRAME 프론트엔드 MVP (dev 백엔드 API 연동)

현재 `sw1029/project_videoManager` 저장소의 **`dev` 브랜치** FastAPI `presentation/schemas.py`, `routers/projects.py`, `routers/versions.py`에 맞춰 작성한 독립적인 프론트 초안입니다. JavaScript + Vite로 구성하여 추가 프레임워크 없이 동작합니다.

## 한 번에 실행 (새 기기 시연)

화면을 빌드해 백엔드가 같은 주소에서 서빙하는 방식입니다. 저장소 루트에서 Windows 는 `scripts\setup.cmd` → `scriptsun.cmd`,
macOS·Linux 는 `sh scripts/setup.sh` → `sh scripts/run.sh` 를 실행하면 `http://127.0.0.1:8000` 이 열립니다.
자세한 내용은 [docs/DEMO.md](../docs/DEMO.md) 를 보세요. 아래는 화면을 고치면서 개발할 때의 방법입니다.

## 개발 실행

**터미널 1 (저장소 `dev` 브랜치 백엔드 루트)**

```bash
python -m venv .venv
source .venv/bin/activate             # Windows: .venv\\Scripts\\activate
pip install -e '.[dev,llm]'           # llm: 자연어 구조화(OpenAI)용
python -m uvicorn --factory frame.presentation.main:create_default_app --reload
```

기본 백엔드는 `http://localhost:8000`이며 `FRAME_STRUCTURER=rule_based` (기본)에서는 일반 자연어가 아닌 문서의 **DSL** 형식으로 구조화됩니다. 일반 자연어 분석은 `FRAME_STRUCTURER=llm`과 별도 서버 설정 및 백엔드용 OpenAI 키가 필요합니다. 프론트 코드·빌드 결과·브라우저 저장소에 비밀키를 넣지 마세요. 시연용 키 입력란은 백엔드를 `FRAME_LLM_RUNTIME_KEY=true` 로 실행하고 서버와 같은 기기에서 화면을 열었을 때만 보이며, 입력한 키는 백엔드 메모리에만 전달됩니다 (브라우저에 저장하지 않음).

**터미널 2 (이 ZIP의 frontend 폴더)**

```bash
npm install
npm run dev
```

브라우저에서 `http://localhost:5173`을 엽니다. Vite dev proxy가 `/api`, `/health` 요청을 FastAPI에 전달하므로, 로컬 시연에서는 CORS 설정 없이 통신할 수 있습니다. 백엔드가 다른 호스트면 `FRAME_API_URL=http://127.0.0.1:8000 npm run dev`와 같이 변경할 수 있습니다.

## 중요: 구조화 입력 안내

- **LLM 모드:** 초기 화면에 들어 있는 자연어 예제를 사용할 수 있습니다. 백엔드 키와 `FRAME_STRUCTURER=llm` 설정이 필요합니다.
- **기본 rule_based 모드:** 아래 DSL 예제 내용을 입력하세요.

```text
scene intro 0-4000 : 바리스타가 컵을 들어 보인다
scene closeup 4000-8000 : 컵 클로즈업
entity barista PERSON
entity cup PROP
in intro: barista, cup
in closeup: cup
cup.color = red
barista holds -> cup @ intro
```

위 예제 입력 시 목표 길이도 **8초**로 맞춰야 합니다.

## 구현된 프론트

- 서버 `/health` 연결 상태 확인
- `POST /api/projects` 프로젝트 생성
- `GET /api/versions/{id}` 장면·엔티티·요구사항 카드 표시
- `POST /api/versions/{id}/generations` 생성 요청 및 새로고침
- `POST /api/versions/{id}/revisions` 엔티티/속성 변경, 영향을 받은 장면 목록 출력
- `GET /api/versions/{id}/checks` 요구사항 반영률·일관성·보존율과 판정 표시
- `/health` 의 입력 방식(`structuring_input`)에 맞는 시연 프리셋(`public/presets.json`)으로 입력란 채우기
- OpenAI API 키 입력란 (`GET`·`PUT`·`DELETE /api/settings/llm-key`, 서버와 같은 기기에서만)
- `npm test`: 프리셋·키 입력 로직 단위 테스트 (node:test)

## 추후 백엔드 이슈와의 연결

- Issue #9: 운영 도메인 CORS와 OpenAPI 클라이언트
- Issue #10, #11: 블록 기반 IR 직접 입력 및 DRAFT 편집
- Issue #12: 이미지 파일 업로드
- Issue #13: 집계 API 및 버전 간 비교 응답
- Issue #14: 완성된 대시보드, 재생성 비디오 비교 뷰어, 트랙 시각화

## 주의

현재 프론트는 백엔드의 **실제 응답**만 사용합니다. Fake 영상 생성 어댑터의 결과와 실제 OpenAI/비디오 생성 모델 결과를 혼동하면 안 됩니다. 자동 상태 폴링, 영상 파일 서빙, 정식 인증, React/Vue 전환은 아직 포함하지 않았습니다.
