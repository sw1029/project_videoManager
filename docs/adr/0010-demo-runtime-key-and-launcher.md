# ADR-0010 시연용 화면 키 입력과 OS 공통 실행 런처

- 상태: 채택 (2026-11-09, 새 기기 시연 준비 반영)

## 맥락
새 컴퓨터에서 시연하려면 백엔드 설치, 프론트 빌드, 환경 변수 설정, 키 파일 경로 지정을 손으로 해야 했다. 터미널 두 개(uvicorn, Vite)를
띄워야 했고, 입력란은 비어 있어 발표 중에 요구사항을 직접 쳐야 했다. 키는 `OPENAI_API_KEY` 또는 키 파일로만 받을 수 있어,
키 파일이 없는 기기에서는 설정을 고치고 서버를 다시 띄워야 했다. 발표 장소의 기기는 Windows · macOS · Linux 어느 것이든 될 수 있다.
프론트 README 는 "프론트에 비밀키를 넣지 말라" 고 약속하고 있다.

## 결정
- **화면 키 입력 (opt-in)**: `FRAME_LLM_RUNTIME_KEY=true` 일 때만 `PUT`·`DELETE /api/settings/llm-key` 로 키를 넣고 뺀다 (기본 꺼짐).
  켜면 키 없이 기동하고, 키가 없는 동안 구조화는 `LLM_UNAVAILABLE` 과 키 넣는 방법을 안내한다. 끄면 기존처럼 키가 없을 때 기동이 실패한다.
- **같은 기기에서만**: 입력·삭제는 loopback(127.0.0.1 · ::1) 요청만 받는다. `FRAME_LLM_RUNTIME_KEY_ALLOW_REMOTE` 로만 풀 수 있고 권장하지 않는다.
  상태 응답의 `runtime_entry` 는 **요청한 기기 기준** 입력 가능 여부, `runtime_entry_enabled` 는 서버 설정이다. 다른 기기 화면은 입력란 대신
  "서버를 실행한 컴퓨터에서 입력" 안내를 보여 준다.
- **메모리에만, 끝 4자리만**: 키는 `LLMKeyStore` 메모리에만 두고 DB·로그·응답에 남기지 않는다. 요청 본문은 `SecretStr`, 형식 오류 응답은 값을
  되풀이하지 않는다. 브라우저는 키를 저장하지 않고 적용 후 입력란을 비운다. 우선순위: 화면 입력 > 설정 > `OPENAI_API_KEY` > 키 파일.
  OpenAI 클라이언트는 호출 때마다 키를 해석하므로 재시작 없이 바뀐다 (`LLMKeySource` 포트).
- **OS 공통 런처**: `scripts/frame_demo.py` (`frame.tools.demo`, 표준 라이브러리만)의 `doctor` · `setup` · `run` · `smoke` 와
  얇은 래퍼(`scripts/*.cmd`, `scripts/*.sh`). `run` 은 빌드된 화면을 `FRAME_FRONTEND_DIST` 로 같은 출처에서 서빙하는 서버 하나만 띄우므로
  실행 시점에 Node.js 가 필요 없다. 시연 상태는 `.demo/`(git 제외)에 두고, `setup` 이 받은 키는 `.demo/openai.key`(POSIX 600)에 둔다.
  `--offline` 은 규칙 기반(DSL), `--lan` 은 다른 기기 보기용(분당 30회 POST 제한 기본값), `--reset` 은 DB·산출물 초기화.
  다른 OS 가 만든 `.venv` 는 지우지 않고 멈춘다. 시연 프리셋은 `frontend/public/presets.json` 에 두고, DSL 프리셋은 계약 테스트로 구조화를 확인한다.
- **OS별 휴대용 번들**: 시연만 할 기기를 위해 `scripts/frame_bundle.py` (`frame.tools.bundle`)가 휴대용 Python
  (astral-sh/python-build-standalone 3.12, 릴리스·SHA-256 고정) + FRAME·의존성 일반 설치 + 빌드된 화면 + 래퍼를
  `frame-demo-<대상>.zip|tar.gz` 로 묶는다 (Windows x64, macOS arm64·x64, Linux x64·arm64). 번들 루트의 `frame-bundle.json` 이 있으면
  런처는 `.venv` 대신 동봉 Python 을 쓰고 `setup` 은 키 저장만 한다. 서버·래퍼는 `python -I` 로 띄워 다른 Python 의
  PYTHONHOME·PYTHONPATH 가 끼어들지 못하게 한다. 대상 OS·CPU 에서만 만들 수 있어(그 플랫폼 휠 설치) CI `bundle` 워크플로가
  러너별로 만들고 풀어서 점검하며, `v*` 태그에서 Release 에 첨부한다. `__pycache__`·C 헤더·옮기면 깨지는 실행 스크립트는 빼고,
  Windows 경로 길이 제한(260자) 때문에 번들 안 가장 긴 경로를 160자 이하로 검사한다.
  macOS 는 공증이 없어 격리 속성을 지워야 하므로 `run.sh` 가 사용자에게 묻고(`[y/N]`) 그 폴더에서만 지운다.
- **검증**: 런처·번들 도구의 OS 분기는 단위 테스트, 실제 설치·기동·흐름은 CI `demo-smoke` 작업이 ubuntu · windows · macos 에서
  `setup` → `smoke` 로, 번들은 `bundle` 작업이 5개 대상에서 `build` → `verify`(시스템 PATH 만 둔 임시 폴더에서 smoke)로 확인한다.

## 결과
- 새 기기 시연이 "Python·Node 설치 → setup → run" 으로 줄고, 번들을 쓰면 "받기 → 압축 풀기 → run" 으로 더 줄어든다.
  키 파일 없이도 화면에서 키를 넣어 바로 시연할 수 있다.
- 번들은 대상마다 약 30MB 이고 코드 서명이 없어 첫 실행 경고가 나온다 (Windows 게시자 확인, macOS 격리). 서명·공증은 비용 문제로 보류.
  휴대용 Python 버전을 올릴 때는 릴리스 태그와 SHA-256 을 함께 바꿔야 한다.
- 인증이 없으므로 `--lan` 에서는 같은 네트워크의 누구나 서버의 키로 분석을 요청할 수 있다. 신뢰할 수 있는 네트워크에서만 쓰도록 문서와 실행 화면에 알린다.
  공개 배포에는 이 기능을 켜지 않는다 (운영 인증·비밀 관리 도입 시 대체).
- iOS·iPadOS 는 서버를 실행할 수 없어 `--lan` 보기 전용으로 지원한다.
- 프론트의 "비밀키를 넣지 말라" 약속은 "코드·빌드·브라우저 저장소에 넣지 말라" 로 구체화된다 (프론트 담당 합의 필요).

## 대안
- 브라우저 저장소(localStorage)에 키 보관: XSS·공용 기기 노출 위험으로 기각.
- 키를 프론트 빌드 환경 변수로 주입: 번들에 키가 박혀 배포물이 비밀이 되므로 기각.
- Docker 만 지원: 발표 기기에 Docker Desktop 설치·실행 부담이 커 보조 수단으로 남김 (`docker/docker-compose.yml`).
- PyInstaller 단일 실행 파일: 파일 하나라 편하지만 OS마다 빌드가 필요한 점은 같고, Windows 백신 오탐이 잦고, uvicorn·SQLAlchemy·openai 의
  숨은 import 설정과 실행마다 임시 폴더 압축 해제가 필요해 기각. 휴대용 Python 번들은 지금의 런처·서버 경로를 그대로 쓴다.
- uv 같은 부트스트래퍼로 첫 실행 때 Python 받기: 패키지는 작지만 발표장 네트워크에 의존해 기각.
