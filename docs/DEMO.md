# FRAME 시연 실행 가이드 (Windows · macOS · Linux)

새 컴퓨터 한 대에서 화면과 API 를 함께 띄워 시연하는 방법이다. 서버 프로세스 하나가 `http://127.0.0.1:8000` 에서
화면(빌드된 프론트)과 API 를 같이 제공하므로 CORS 설정이나 터미널 두 개가 필요 없다. 입력란에는 시연 프리셋이 미리 채워진다.

| 모드 | 실행 | 입력 | 필요한 것 |
|---|---|---|---|
| LLM (기본) | `run` | 자연어 요구사항 | 인터넷, OpenAI API 키 |
| 오프라인 | `run --offline` | DSL 요구사항 (프리셋 제공) | 없음 |

> iPhone·iPad(iOS·iPadOS)에서는 서버를 실행할 수 없다 (Python 런타임과 컴파일된 의존성을 설치할 수 없음).
> 같은 네트워크의 컴퓨터에서 `run --lan` 으로 띄우고 Safari 로 접속해 **보기·조작용**으로 쓴다 ([7절](#7-다른-기기iphoneipad다른-pc에서-보기)).

## 1. 준비물

- **Python 3.11 이상**
- **Node.js 18 이상** — 화면을 빌드하는 `setup` 에만 필요하고, 실행(`run`)에는 필요 없다
- **Git**

| OS | 설치 명령 (예) |
|---|---|
| Windows 10/11 | `winget install Python.Python.3.12` · `winget install OpenJS.NodeJS.LTS` · `winget install Git.Git` |
| macOS | `xcode-select --install` (Git) · `brew install python@3.12 node` (또는 python.org · nodejs.org 설치 파일) |
| Ubuntu 24.04 / Debian 12 | `sudo apt install python3 python3-venv git` + Node.js 20 ([NodeSource](https://github.com/nodesource/distributions) 또는 nvm) |
| Ubuntu 22.04 | `sudo apt install python3.11 python3.11-venv git` (기본 3.10 은 지원하지 않음) + Node.js 20 |
| Fedora | `sudo dnf install python3 nodejs git` |

Windows 는 설치 후 터미널을 새로 열어야 `py`·`node` 가 잡힌다. "Python was not found; run without arguments to install from the
Microsoft Store" 가 나오면 설정 → 앱 → 고급 앱 설정 → **앱 실행 별칭** 에서 python 별칭을 끄거나 위 명령으로 설치한다.

## 2. 받기

```bash
git clone https://github.com/sw1029/project_videoManager.git
cd project_videoManager
git checkout dev
```

WSL 에서는 Windows 폴더(`/mnt/c/...`)가 아니라 리눅스 쪽(`~/`)에 따로 clone 한다. 한 폴더를 Windows 와 WSL 이 같이 쓰면
`.venv`·`node_modules` 가 서로 맞지 않는다 (`setup` 은 다른 OS 용 `.venv` 를 지우지 않고 멈춘다).

## 3. 설치 (한 번)

| OS | 명령 |
|---|---|
| Windows (명령 프롬프트·PowerShell) | `scripts\setup.cmd` |
| macOS · Linux | `sh scripts/setup.sh` |

하는 일: 가상환경 `.venv` 생성 → `pip install -e ".[llm]"` → `frontend` 에서 `npm ci` 와 `npm run build` → OpenAI 키 입력(선택).
키 입력은 화면에 보이지 않으며, 건너뛰려면 Enter 를 누른다. 다시 실행해도 안전하다 (있는 것은 그대로 쓰고 필요한 것만 다시 한다).

| 옵션 | 의미 |
|---|---|
| `--key-file 경로` | 키가 든 파일(키만 있는 줄 또는 `OPENAI_API_KEY=...` 줄)에서 키를 읽어 `.demo/openai.key` 로 복사 |
| `--no-key` | 키를 묻지 않음 (실행 후 화면에서 입력) |
| `--skip-frontend` | 화면 빌드 생략 (이미 `frontend/dist` 가 있을 때) |

설치를 확인하려면 자동 점검을 돌린다. 오프라인 흐름(구조화 → 생성 → 검수 → 결과 주소)과 LLM 모드의 키 입력 경로를
외부 호출 없이 확인하고 끝난다 (CI 가 같은 점검을 Windows · macOS · Linux 에서 돌린다).

```bash
python scripts/frame_demo.py smoke       # Windows: py -3 scripts\frame_demo.py smoke
```

환경만 점검하려면 `python scripts/frame_demo.py doctor` (Windows: `py -3 scripts\frame_demo.py doctor`).

## 4. 실행

| OS | 명령 |
|---|---|
| Windows | `scripts\run.cmd` |
| macOS · Linux | `sh scripts/run.sh` |

브라우저가 `http://127.0.0.1:8000` 으로 열린다 (Linux 는 데스크톱 화면이 있을 때만 자동으로 연다). 끝내려면 터미널에서 `Ctrl+C`
(Windows 는 이어서 나오는 "일괄 작업을 끝내시겠습니까 (Y/N)?" 에 `Y`).

| 옵션 | 의미 |
|---|---|
| `--offline` | 키·인터넷 없이 규칙 기반(DSL)으로 시연. 프리셋도 DSL 로 바뀐다 |
| `--port 8010` | 8000 포트를 다른 프로그램이 쓸 때 |
| `--lan` | 같은 네트워크의 다른 기기에서 접속 허용 ([7절](#7-다른-기기iphoneipad다른-pc에서-보기)) |
| `--reset` | 시연 DB·산출물을 지우고 시작 (키 파일은 유지) |
| `--no-browser` | 브라우저를 자동으로 열지 않음 |

프로젝트와 버전은 `.demo/frame.db` 에 남아 다시 실행해도 이어서 볼 수 있다.

## 5. OpenAI API 키 넣는 방법

세 가지 중 하나면 된다. 여러 개가 있으면 **화면 입력 > 환경 변수 > 키 파일** 순으로 쓴다.

1. **설치할 때 입력**: `setup` 이 물을 때 붙여 넣거나 `--key-file` 을 준다. `.demo/openai.key` 에 저장된다
   (git 제외, macOS·Linux 는 소유자만 읽기 권한 600, Windows 는 사용자 폴더 권한을 따른다).
2. **화면에서 입력**: `01 · 새 프로젝트 생성` 패널의 **OpenAI API 키** 칸에 넣고 `적용`.
   키는 브라우저에 저장하지 않고 서버 메모리에만 두며, 서버를 다시 시작하면 사라진다.
   서버를 실행한 컴퓨터에서 연 화면에서만 입력·삭제할 수 있다 (다른 기기에서는 입력란 대신 안내가 보이고, 서버도 403 으로 거부한다).
3. **환경 변수**: 실행 전에 `OPENAI_API_KEY` 를 설정한다.

화면과 API 응답에는 키의 끝 4자리(`sk-...abcd`)만 보인다. 공용 컴퓨터라면 시연 후 `.demo/openai.key` 를 지운다.
키를 코드·커밋·화면 소스·이슈에 붙여 넣지 않는다.

## 6. 시연 순서 (권장)

1. 오른쪽 위가 **API 연결됨** 인지, `01` 패널의 입력 방식 표시(자연어 / DSL)를 확인한다. LLM 모드에서 키가 없으면 키 칸에 넣는다.
2. **시연 프리셋** 을 고르면 제목·요구사항·목표 길이가 채워진다 → **요구사항 분석 시작** → `DRAFT` 와 장면·객체·조건 카드를 보여 준다.
3. **영상 생성 요청** → `COMPLETED` → `05 · 품질 검수` 의 반영률·일관성 결과를 보여 준다.
4. `04 · 선택적 수정` 에서 객체 속성을 바꿔(예: 컵 색) **수정 버전 만들기** → 영향받는 장면 목록 → 수정 버전 생성 → 보존율 비교.

- 원본 버전을 먼저 생성(3단계)해야 수정 버전에서 바뀌지 않은 장면을 재사용하는 **선택적 재생성** 을 보여 줄 수 있다.
- 현재 영상 생성기는 시연용 대체 구현(Fake)이라 실제 영상 대신 결과 정보(JSON)가 저장된다. 실제 모델은 어댑터만 바꿔 붙인다.
- LLM 구조화는 요청마다 수 초가 걸린다. 발표 중 네트워크가 불안하면 `--offline` 으로 같은 흐름을 DSL 프리셋으로 보여 준다.

## 7. 다른 기기(iPhone·iPad·다른 PC)에서 보기

```bash
sh scripts/run.sh --lan          # Windows: scripts\run.cmd --lan
```

터미널에 `http://192.168.x.x:8000` 같은 주소가 나오면 같은 Wi-Fi 의 기기 브라우저(Safari 등)에서 연다.

- 처음 실행할 때 방화벽 확인 창이 뜬다. Windows 는 **개인 네트워크** 만 허용, macOS 는 **허용**. Linux 에서 ufw 를 쓰면 `sudo ufw allow 8000/tcp`.
- 키 입력·삭제는 서버를 실행한 컴퓨터에서만 된다. `FRAME_LLM_RUNTIME_KEY_ALLOW_REMOTE` 는 켜지 않는다.
- **인증이 없는 시연용 서버** 다. 같은 네트워크의 누구나 프로젝트를 만들고, 서버에 설정된 키로 LLM 분석을 요청할 수 있다.
  믿을 수 있는 네트워크에서만 쓰고 시연이 끝나면 끈다. `--lan` 에서는 IP 별 POST 요청을 분당 30회로 제한한다 (`FRAME_RATE_LIMIT_PER_MINUTE` 로 변경).

## 8. 문제 해결

| 증상 | 해결 |
|---|---|
| `Python 3.11+ is required` | 1절대로 설치. Ubuntu 22.04 는 `python3.11`, `python3.11-venv` 를 설치 |
| `가상환경을 만들지 못했습니다` (Ubuntu·Debian) | 안내된 `sudo apt install python3.X-venv` 후 `setup` 다시 실행 (반쯤 만들어진 `.venv` 는 다시 만든다) |
| `.venv 가 다른 운영체제에서 만들어졌습니다` | 이 OS 에서는 저장소를 따로 clone (2절). 필요 없으면 `.venv` 폴더를 지우고 다시 실행 |
| 화면 빌드 실패 | `node --version` 이 18 이상인지 확인, `frontend/node_modules` 를 지우고 `setup` 다시 실행 |
| `포트 8000 를 이미 쓰고 있습니다` | `--port 8010` |
| 화면에 "API 미연결" | 서버 터미널의 오류를 확인. 주소가 `http://127.0.0.1:8000` 인지 확인 |
| 분석이 `LLM_UNAVAILABLE` 로 실패 | 키가 없거나 잘못되었거나 연결이 안 된다. 오류 메시지를 보고 5절 방법으로 키를 넣은 뒤 다시 분석 |
| `LLM_TIMEOUT` · `LLM_RATE_LIMITED` · `LLM_BAD_RESPONSE` | 잠시 후 다시 분석하거나 `--offline` 으로 시연 |
| 다른 기기에서 키 입력이 거부됨 (403) | 정상 동작. 서버를 실행한 컴퓨터에서 입력 |
| 프록시가 있는 회사망 | 런처의 로컬 확인은 프록시를 거치지 않는다. OpenAI 호출에는 `HTTPS_PROXY` 를 설정 |
| 처음부터 다시 | `run --reset` (DB·산출물 삭제, 키 유지). 키까지 지우려면 `.demo` 폴더 삭제 |

## 9. 파일 위치

| 경로 | 내용 | git |
|---|---|---|
| `.venv/` | 백엔드 가상환경 | 제외 |
| `frontend/dist/` | 빌드된 화면 (`setup` 이 생성) | 제외 |
| `.demo/frame.db`, `.demo/artifacts/` | 시연 DB 와 산출물 | 제외 |
| `.demo/openai.key` | 설치 때 저장한 키 | 제외 |

직접 실행하고 싶다면 런처가 쓰는 설정은 `src/frame/tools/demo.py` 의 `demo_env()` 에 모여 있다
(`FRAME_STRUCTURER`, `FRAME_LLM_RUNTIME_KEY=true`, `FRAME_JOBS=inline`, SQLite, `FRAME_FRONTEND_DIST=frontend/dist`).
