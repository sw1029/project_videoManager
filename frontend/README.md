# FRAME 프론트엔드 — 사용자 친화적 UI 리디자인 (v0.2)

이 폴더는 팀 저장소 `sw1029/project_videoManager`의 `frontend/`에 **덮어쓸 파일**입니다. Vite + Vanilla JS이며 추가 UI 라이브러리가 없습니다.

## 변경 내용
- 민트·딥그린의 밝은 UI, 4단계 작업 흐름, 전후 비교 화면, 객체 타임라인
- 기존 FastAPI API 기반 프로젝트 생성, 구조화 IR, 생성 요청, 버전/수정, 검수 조회
- `/health` 구조화 입력 모드에 따른 자연어/DSL 예시 전환
- `STRUCTURING`·`QUEUED`·`PROCESSING` 시 자동 폴링(2.5초)
- 실제 `video_url`이 `video/*`인 경우 영상 플레이어, Fake JSON인 경우 명확한 안내
- 엔티티 트랙 API 요청, 없으면 장면의 등장 정보로 타임라인 표시
- 선택한 엔티티의 기존 요구속성 수정 및 변경 범위(`SCENES` / `ALL_APPEARANCES`) 전달
- 생성 결과가 없는 최초 화면은 **디자인 예시 이미지**로 명확하게 표기

## 한 번에 실행 (새 기기 시연)

화면을 빌드해 백엔드가 같은 주소에서 서빙하는 방식입니다. 저장소 루트에서 Windows 는 `scripts\setup.cmd` → `scripts\run.cmd`,
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

터미널 2:

```bash
cd frontend
npm install
npm run dev
```

브라우저 `http://localhost:5173`로 접속합니다. Vite 프록시가 `/api`, `/health`를 `http://localhost:8000`으로 전달합니다.

## 현재 범위와 제한
- '유지할 요소' 체크박스와 자유형 요청 설명은 **UI 안내용**이며 현재 백엔드 `POST /revisions`가 받는 필드에 포함되지 않습니다. 실제 API에는 `entity_key`, `predicate`, `new_value`, `scope`, `scene_keys`만 보냅니다.
- 생성 버튼은 `DRAFT`에서만 작동합니다. Fake 모드의 산출물은 MP4가 아니라 JSON입니다.
- 이미지 예시는 기능 증빙이 아니라 디자인 시연용입니다. 실제 데이터가 생기면 예시 대신 서버 응답을 표시합니다.
- 타임라인의 기본 표시 영역은 장면/요구조건 데이터에 따른 구간입니다. 실제 객체 트래킹 결과가 있으면 해당 엔티티에 적용됩니다.
- 비교 보기는 버전별 영상 URL을 재생하나 영상 재생 위치 동기화·차이 강조는 미구현입니다.
- 새 수정 버전 생성은 실제 부분 영상 렌더링을 보장하지 않습니다. 별도 생성 요청 및 실제 모델이 필요합니다.

## GitHub에 반영
이미 `feature-frontend-mvp` 브랜치가 있다면 새 브랜치 `feature-frontend-redesign`에서 작업 후 PR을 `dev`로 보내는 것을 권장합니다. 아래 명령어는 **다운로드한 ZIP을 풀고 압축 안의 `frontend`를 팀 저장소의 `frontend`에 덮어쓴 이후** 실행합니다.

```bash
git status
git add frontend/
git commit -m "feat(frontend): redesign FRAME creator workspace"
git push -u origin feature-frontend-redesign
```

참고: 저장소가 업데이트되면 API 계약에 따라 코드를 재검증해야 합니다.

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
