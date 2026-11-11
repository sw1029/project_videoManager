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

## 실행
터미널 1 (팀 프로젝트 루트):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python -m uvicorn --factory frame.presentation.main:create_default_app --reload
```

기본 생성기는 Fake이며, 규칙 기반 구조화기는 DSL 입력이 필요합니다. OpenAI 자연어 구조화 시 백엔드에만 `FRAME_STRUCTURER=llm`, `OPENAI_API_KEY` 등 환경 변수를 설정해야 합니다.

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
