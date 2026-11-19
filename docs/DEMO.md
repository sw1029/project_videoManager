# FRAME 시연 실행 가이드 (Windows · macOS · Linux)

새 컴퓨터 한 대에서 화면과 API 를 함께 띄워 시연하는 방법이다. 서버 프로세스 하나가 `http://127.0.0.1:8000` 에서
화면(빌드된 프론트)과 API 를 같이 제공하므로 CORS 설정이나 터미널 두 개가 필요 없다. 입력란에는 시연 프리셋이 미리 채워진다.

| 모드 | 실행 | 입력 | 영상 | 필요한 것 |
|---|---|---|---|---|
| LLM (기본) | `run` | 자연어 요구사항 | 시연용 가짜 생성기 (결과 정보만) | 인터넷, OpenAI API 키 |
| 오프라인 | `run --offline` | DSL 요구사항 (프리셋 제공) | 시연용 가짜 생성기 | 없음 |
| 실제 영상 | `run --video` | 자연어 요구사항 | OpenRouter 로 실제 생성 (**장면마다 요금**) | 인터넷, OpenAI · OpenRouter API 키, ffmpeg (`setup --video`) |

API 키는 **키 도우미**(`keys`)로 넣고 확인한다 — Windows 는 `scripts\keys.cmd` 를 더블클릭, macOS · Linux 는 `sh scripts/keys.sh`
([5절](#5-api-키-넣는-방법-openai--openrouter)).

> iPhone·iPad(iOS·iPadOS)에서는 서버를 실행할 수 없다 (Python 런타임과 컴파일된 의존성을 설치할 수 없음).
> 같은 네트워크의 컴퓨터에서 `run --lan` 으로 띄우고 Safari 로 접속해 **보기·조작용**으로 쓴다 ([7절](#7-다른-기기iphoneipad다른-pc에서-보기)).

실행 방법은 두 가지다. 시연만 할 기기는 **0절 번들**(Python·Node 설치 불필요)을, 코드를 고치는 기기는 1~4절 저장소 방식을 쓴다.

## 0. 설치 없이 실행 — OS별 번들 (권장)

번들은 휴대용 Python 3.12 · FRAME · 의존성 · 빌드된 화면을 한 파일로 묶은 것이다. 받아서 압축만 풀면 실행된다
(Python·Node.js·Git 설치와 관리자 권한은 필요 없다. 인터넷과 키는 LLM 분석(OpenAI 키)과 실제 영상
`run --video`(OpenRouter 키 + 따로 설치한 ffmpeg)에만 필요하다).

| 기기 | 파일 | 실행 |
|---|---|---|
| Windows 10/11 (x64) | `frame-demo-windows-x64.zip` | `run.cmd` 더블클릭 |
| macOS Apple Silicon (M1 이후) | `frame-demo-macos-arm64.tar.gz` | 터미널에서 `sh run.sh` |
| macOS Intel | `frame-demo-macos-x64.tar.gz` | 터미널에서 `sh run.sh` |
| Linux x86_64 (Ubuntu 22.04 이상 등 glibc 배포판) | `frame-demo-linux-x64.tar.gz` | `sh run.sh` |
| Linux ARM64 | `frame-demo-linux-arm64.tar.gz` | `sh run.sh` |

**받는 곳**: 저장소 Releases (`v*` 태그를 올리면 CI 가 첨부) 또는 Actions 의 `bundle` 실행 결과(Artifacts, 30일 보관).
파일마다 있는 `.sha256` 으로 받은 파일이 맞는지 확인할 수 있다. 크기는 압축 기준 약 30MB 안팎이다 (Windows 28MB).

1. **압축 풀기** — 바탕화면처럼 쓰기 가능한 폴더에 푼다. DB·결과는 풀린 폴더의 `.demo/` 에 저장된다.
   - Windows: 받은 zip 의 속성에서 **차단 해제** 를 체크한 뒤 "압축 풀기" 하면 첫 실행 경고가 나오지 않는다.
     "파일 이름이 너무 깁니다" 오류가 나면 `C:\frame` 처럼 짧은 경로에 푼다 (Windows 경로 길이 260자 제한).
   - macOS · Linux: Finder 에서 더블클릭하거나 `tar -xzf frame-demo-<대상>.tar.gz`
2. **실행** — Windows 는 `run.cmd`, macOS · Linux 는 풀린 폴더에서 `sh run.sh`. 브라우저가 `http://127.0.0.1:8000` 으로 열린다.
   옵션은 저장소 방식과 같다 (`--offline`, `--lan`, `--port`, `--reset`, `--no-browser`, `--video`, [4절](#4-실행)).
3. **키** — 키 도우미 `keys.cmd`(더블클릭) / `sh keys.sh` 로 OpenAI · OpenRouter 키를 넣고 확인한다. 저장한 키는 서버를
   시작할 때 읽으므로, 실행 중에 저장했다면 `Ctrl+C` 로 끝내고 다시 실행한다. OpenAI 키는 화면의 키 칸에 넣어도 된다
   (바로 쓰인다, [5절](#5-api-키-넣는-방법-openai--openrouter)).
4. **실제 영상** — `run.cmd --video` / `sh run.sh --video`. 번들에는 ffmpeg 가 들어 있지 않아(크기 · 라이선스) 먼저 설치해 PATH 에
   둔다: Windows `winget install Gyan.FFmpeg`, macOS `brew install ffmpeg`, Linux `sudo apt install ffmpeg`.

처음 실행할 때 나오는 경고:

- **Windows**: "게시자를 확인할 수 없습니다" 가 나오면 [실행]. 번들은 코드 서명을 하지 않았다.
- **macOS**: 브라우저로 받은 파일에는 격리 속성(`com.apple.quarantine`)이 붙고, 동봉 Python 은 Apple 공증을 받지 않아 그대로는 막힌다.
  `run.sh` 가 이 폴더의 격리 속성을 지울지 묻고(`[y/N]`), `y` 일 때만 지운다. 직접 하려면 `xattr -dr com.apple.quarantine <풀린 폴더>`.
  터미널에서 `curl -LO <파일 주소>` 로 받으면 격리 속성이 붙지 않는다.
- **Linux**: 경고 없음. Alpine 같은 musl 배포판은 지원하지 않는다.

번들 폴더에서 환경을 점검하려면 Windows `python\python.exe -I -m frame.tools.demo --root . doctor`,
macOS · Linux `python/bin/python3 -I -m frame.tools.demo --root . doctor`.

## 1. 준비물 (저장소 방식)

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
| Windows (명령 프롬프트·PowerShell) | `scripts\setup.cmd` (탐색기에서 더블클릭해도 된다 — 끝에서 창이 멈춘다) |
| macOS · Linux | `sh scripts/setup.sh` |

하는 일: 가상환경 `.venv` 생성 → `pip install -e ".[llm]"` → `frontend` 에서 `npm ci` 와 `npm run build` → OpenAI 키 입력(선택).
키 입력은 화면에 보이지 않으며, 건너뛰려면 Enter 를 누른다. 다시 실행해도 안전하다 (있는 것은 그대로 쓰고 필요한 것만 다시 한다).

| 옵션 | 의미 |
|---|---|
| `--key-file 경로` | 키가 든 파일(키만 있는 줄 또는 `OPENAI_API_KEY=...` 줄)에서 키를 읽어 `.demo/openai.key` 로 복사 |
| `--no-key` | 키를 묻지 않음 (실행 후 화면에서 입력, 또는 나중에 `keys`) |
| `--skip-frontend` | 화면 빌드 생략 (이미 `frontend/dist` 가 있을 때) |
| `--video` | 실제 영상 생성 준비: `pip install -e ".[llm,video]"` 로 ffmpeg(imageio-ffmpeg)를 함께 설치하고, OpenRouter 키도 묻는다 |
| `--openrouter-key-file 경로` | OpenRouter 키(`sk-or-...` 또는 `OPENROUTER_API_KEY=...` 줄)를 읽어 `.demo/openrouter.key` 로 복사 |

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
| `--reset` | 시연 DB·산출물을 지우고 시작 (키 파일은 유지. `--video` 와 함께면 `.demo/video` 만) |
| `--no-browser` | 브라우저를 자동으로 열지 않음 |
| `--video` | 실제 영상 생성 (OpenRouter, **장면마다 요금**). 아래 참고 |

프로젝트와 버전은 `.demo/frame.db` 에 남아 다시 실행해도 이어서 볼 수 있다.

**실제 영상 모드** (`run --video`, [ADR-0037](adr/0037-demo-launcher-key-helper-and-real-video-mode.md)): 먼저 `setup --video` 를 한 번
실행하고(ffmpeg 설치 · OpenRouter 키), OpenAI 키도 넣는다(분석 · 인물 일관성 · 검수에 쓴다).

- 영상 생성은 OpenRouter(`FRAME_GENERATOR=external_api`, 기본 모델 `alibaba/wan-3.0`), 장면 클립 잇기는 ffmpeg 다.
  요금은 **영상 생성 요청을 누를 때만** 든다 (분석 · 화면 보기에는 들지 않는다).
- 셸에서 정하지 않았으면 시연에 맞춘 값으로 띄운다: 해상도 `480p`(720p 의 절반 요금), 검수기
  `vlm_requirement,vlm_consistency,ssim_preservation`, 생성 시간 제한 900초 (셸에 `FRAME_VIDEO_TIMEOUT_S` 가 있으면 그 값 +
  300초). 셸의 `FRAME_VIDEO_RESOLUTION` 등이 있으면 그 값을 쓴다.
- 처리는 백그라운드에서 돌고 화면이 진행 상태를 보여 준다 (장면 하나에 몇 분 걸린다).
- 프로젝트 · 영상은 `.demo/video/` 에 **따로** 저장된다. 서버는 기동할 때 끝나지 않은 작업을 다시 실행하므로, 가짜 생성기로 쓰던
  DB 를 같이 쓰면 거기 남은 작업이 유료 생성기로 돌 수 있어서다.
- 생성 중에 서버를 끄면 다음 실행 때 이어서 진행한다. 이미 제출한 장면은 다시 사지 않고 이어 받는다 (ADR-0013).
- 시작 전에 OpenRouter 키와 ffmpeg 가 없으면 무엇을 할지 알리고 멈춘다 (`--offline` 과는 함께 쓸 수 없다).

## 5. API 키 넣는 방법 (OpenAI · OpenRouter)

| 키 | 쓰는 곳 | 키 파일 | 환경 변수 | 만드는 곳 |
|---|---|---|---|---|
| OpenAI (`sk-...`) | 자연어 요구사항 분석 · 인물 일관성 · 품질 검수(VLM) | `.demo/openai.key` | `OPENAI_API_KEY` | platform.openai.com/api-keys |
| OpenRouter (`sk-or-...`) | 실제 영상 생성 (`run --video`) | `.demo/openrouter.key` | `OPENROUTER_API_KEY` | openrouter.ai/keys |

**키 도우미** (`keys`) — 가장 쉬운 방법이다. 의존성을 설치하기 전에도 돈다.

| OS | 명령 |
|---|---|
| Windows | `scripts\keys.cmd` (탐색기에서 더블클릭해도 된다 — 끝에서 창이 멈춘다) |
| macOS · Linux | `sh scripts/keys.sh` |
| 번들 | `keys.cmd` / `sh keys.sh` |

터미널에서 부르면 메뉴가 나온다: `1` OpenAI 키 넣기 · `2` OpenRouter 키 넣기 · `c` 통하는지 확인 · `d` 지우기 · `q` 끝내기.
키를 붙여 넣어도 화면에 보이지 않는다. 다른 서비스의 키를 잘못된 자리에 넣으면(OpenAI 자리에 `sk-or-...`) 받지 않고 어디에 넣을지 알린다.
끝낼 때 다음에 할 일(`run`, `run --video`)을 보여 준다.

| 옵션 (터미널 없이) | 의미 |
|---|---|
| `--status` | 상태만 보기 (터미널이 아닐 때 — CI · 파이프 — 는 옵션이 없어도 이것만 한다) |
| `--check` | 키가 통하는지 온라인으로 확인. 돈이 들지 않는 조회 하나씩: OpenAI 는 모델 목록, OpenRouter 는 키 정보(쓴 금액 · 남은 한도). 거절된 키가 있으면 종료 코드 1 |
| `--openai-file 경로` · `--openrouter-file 경로` | 키가 든 파일에서 읽어 `.demo` 에 저장 |
| `--remove openai` · `--remove openrouter` | 저장한 키 파일 지우기 (둘 다면 `--remove openai --remove openrouter`) |

확인할 때 키는 **서버가 그 키를 보낼 주소**로만 보낸다. 주소는 설치된 서버 설정에서 받으므로(`FRAME_LLM_BASE_URL` ·
`FRAME_VIDEO_BASE_URL` 로 바꾼 주소도 따른다) 확인은 `setup` 뒤에 된다 — 키를 넣고 보는 것은 설치 전에도 된다.
https 가 아니면 이 기기의 로컬 서버 주소일 때만 보내고, 키 헤더는 리다이렉트로 넘기지 않는다 (어댑터와 같은 공용 전송).
OpenAI 주소로 가는 OpenRouter 키(`OPENAI_API_KEY` 의 `sk-or-...` — 서버도 OpenAI 로 보내지 않는다), OpenRouter 자리의
OpenAI 키, 공백 · 줄바꿈이 섞인 키는 보내지 않고 '거절됨' 으로 알린다. (`FRAME_LLM_BASE_URL` 을 OpenRouter 로 돌렸으면 서버는
OpenAI 자리의 `sk-or-` 키를 쓰므로 확인도 그 주소로 보낸다.) 모델 목록 권한을 뺀(권한을 줄인) OpenAI 키는 분석에는 쓸 수 있어
'확인 못 함' 으로 알린다. 키 파일 변수(`FRAME_*_KEY_FILE`)에 경로 대신 키를 넣었으면 그 값을 보이지 않고 끝 네 글자로 알린다.
확인 중에 `Ctrl+C` 를 누르면 메뉴로 돌아간다 (옵션으로 불렀으면 종료 코드 130).

그 밖의 방법:

1. **설치할 때 입력**: `setup` 이 OpenAI 키를 묻고(`setup --video` 면 OpenRouter 키도), `--key-file` · `--openrouter-key-file` 로 파일을 줄 수도 있다.
2. **화면에서 입력 (OpenAI 키만)**: `새 영상 만들기`(요구사항 단계)의 **OpenAI API 키** 칸에 넣고 `적용`.
   키는 브라우저에 저장하지 않고 서버 메모리에만 두며, 서버를 다시 시작하면 사라진다.
   서버를 실행한 컴퓨터에서 연 화면에서만 입력·삭제할 수 있다 (다른 기기에서는 입력란 대신 안내가 보이고, 서버도 403 으로 거부한다).
   OpenRouter 키는 화면에서 넣지 않는다 — 서버가 기동할 때 읽는다.
3. **환경 변수**: 실행 전에 `OPENAI_API_KEY` · `OPENROUTER_API_KEY` 를 설정한다. 키 파일 경로를 주는
   `FRAME_LLM_KEY_FILE` · `FRAME_VIDEO_KEY_FILE` 도 된다 (상대 경로는 저장소 · 번들 폴더 기준).

여러 곳에 있으면 실행기가 띄운 서버는 **(OpenAI 만) 화면 입력 > 환경 변수(`OPENAI_API_KEY` · `OPENROUTER_API_KEY`) >
`.demo` 키 파일 > `FRAME_LLM_KEY_FILE` · `FRAME_VIDEO_KEY_FILE` 의 파일** 순으로 쓴다. `.demo` 의 키 파일은 그 서비스의 키가
들어 있을 때만 서버에 넘긴다 (아니면 셸의 `FRAME_*_KEY_FILE` 을 덮지 않는다). 도우미 · `doctor` · `run --video` 도 그 순서로 본다.

키 파일은 git 에서 제외되고, macOS·Linux 는 소유자만 읽기 권한 600, Windows 는 사용자 폴더 권한을 따른다.
화면 · 터미널 · API 응답에는 키의 끝 4자리(`sk-...abcd`, `sk-or-...abcd`)만 보인다. 공용 컴퓨터라면 시연 후
`keys --remove openai --remove openrouter` (또는 `keys` 메뉴의 `d`)로 지운다.
저장한 키는 폴더 안에 암호화되지 않은 채 있다. 준비해 둔 폴더(번들 포함)를 복사하거나 USB·드라이브로 넘기기 전에
`.demo` 폴더를 지운다 — 지우지 않으면 키가 함께 넘어간다 (`setup`·`keys`·`doctor` 가 같은 안내를 낸다).
키를 코드·커밋·화면 소스·이슈에 붙여 넣지 않는다.

## 6. 시연 순서 (권장)

1. 오른쪽 위가 **API 연결됨** 인지, `01` 패널의 입력 방식 표시(자연어 / DSL)를 확인한다. LLM 모드에서 키가 없으면 키 칸에 넣는다.
2. **시연 프리셋** 을 고르면 제목·요구사항·목표 길이가 채워진다 → **요구사항 분석 시작** → `DRAFT` 와 장면·객체·조건 카드를 보여 준다.
3. **영상 생성 요청** → `COMPLETED` → `05 · 품질 검수` 의 반영률·일관성 결과를 보여 준다.
4. `04 · 선택적 수정` 에서 객체 속성을 바꿔(예: 컵 색) **수정 버전 만들기** → 영향받는 장면 목록 → 수정 버전 생성 → 보존율 비교.

- 원본 버전을 먼저 생성(3단계)해야 수정 버전에서 바뀌지 않은 장면을 재사용하는 **선택적 재생성** 을 보여 줄 수 있다.
- 기본 영상 생성기는 시연용 대체 구현(Fake)이라 실제 영상 대신 결과 정보(JSON)가 저장된다. 실제 영상은 `run --video`
  (4절, 장면마다 OpenRouter 요금)로 만든다.
- LLM 구조화는 요청마다 수 초가 걸린다. 발표 중 네트워크가 불안하면 `--offline` 으로 같은 흐름을 DSL 프리셋으로 보여 준다.

## 7. 다른 기기(iPhone·iPad·다른 PC)에서 보기

```bash
sh scripts/run.sh --lan          # Windows: scripts\run.cmd --lan
```

터미널에 `http://192.168.x.x:8000` 같은 주소가 나오면 같은 Wi-Fi 의 기기 브라우저(Safari 등)에서 연다.

- 처음 실행할 때 방화벽 확인 창이 뜬다. Windows 는 **개인 네트워크** 만 허용, macOS 는 **허용**. Linux 에서 ufw 를 쓰면 `sudo ufw allow 8000/tcp`.
- 키 입력·삭제는 서버를 실행한 컴퓨터에서만 된다. 셸에 `FRAME_LLM_RUNTIME_KEY_ALLOW_REMOTE=true` 가 있어도 `--lan` 은 끄고 띄운다.
- **인증이 없는 시연용 서버** 다. 같은 네트워크의 누구나 프로젝트를 만들고, 서버에 설정된 키로 LLM 분석을 요청할 수 있다.
  믿을 수 있는 네트워크에서만 쓰고 시연이 끝나면 끈다. `--lan` 에서는 IP 별 POST 요청을 분당 30회로 제한한다
  (`FRAME_RATE_LIMIT_PER_MINUTE` 로 바꾼다. 0 이나 빈 값이면 30 으로 띄운다 — 제한 없이는 열지 않는다).
- 시작할 때 터미널에 적용된 요청 제한이 나온다. 셸에 `FRAME_GENERATOR` 가 실제 생성기로 잡혀 있으면 그것도 경고로 나온다
  (다른 기기의 요청도 이 서버의 키와 자원으로 영상을 만든다).
- 런처 없이 서버를 역방향 프록시(nginx 등) 뒤에 둘 때는 uvicorn 을 `--proxy-headers` 로 띄운다. 그러지 않으면 모든 요청이
  서버와 같은 기기에서 온 것으로 보여, 다른 기기에서도 키를 넣을 수 있고 요청 제한도 모든 사용자를 한 주소로 센다.

## 8. 문제 해결

| 증상 | 해결 |
|---|---|
| `Python 3.11+ is required` | 1절대로 설치. Ubuntu 22.04 는 `python3.11`, `python3.11-venv` 를 설치 |
| `가상환경을 만들지 못했습니다` (Ubuntu·Debian) | 안내된 `sudo apt install python3.X-venv` 후 `setup` 다시 실행 (반쯤 만들어진 `.venv` 는 다시 만든다) |
| `.venv 가 다른 운영체제에서 만들어졌습니다` | 이 OS 에서는 저장소를 따로 clone (2절). 필요 없으면 `.venv` 폴더를 지우고 다시 실행 |
| 화면 빌드 실패 | `node --version` 이 18 이상인지 확인, `frontend/node_modules` 를 지우고 `setup` 다시 실행 |
| `포트 8000 를 이미 쓰고 있습니다` | `--port 8010` |
| `서버가 아직 준비 중입니다` 가 나오고 시작이 오래 걸림 | 지난 실행에서 분석 도중에 끝낸 버전을 시작하면서 다시 분석하는 중이다 (LLM 응답을 기다리며, 길어도 5분). 끝나면 다음 실행부터는 바로 뜬다. 기다리지 않으려면 `Ctrl+C` 뒤 `run --offline` 으로 한 번 실행하거나 (남은 분석이 정리된다) `run --reset` |
| 화면에 "API 미연결" | 서버 터미널의 오류를 확인. 주소가 `http://127.0.0.1:8000` 인지 확인 |
| 분석이 `LLM_UNAVAILABLE` 로 실패 | 키가 없거나 잘못되었거나 연결이 안 된다. 오류 메시지를 보고 5절 방법으로 키를 넣은 뒤 다시 분석 |
| `LLM_TIMEOUT` · `LLM_RATE_LIMITED` · `LLM_BAD_RESPONSE` | 잠시 후 다시 분석하거나 `--offline` 으로 시연 |
| 다른 기기에서 키 입력이 거부됨 (403) | 정상 동작. 서버를 실행한 컴퓨터에서 입력 |
| `run --video`: `OpenRouter 키가 없습니다` | 키 도우미(`keys`)의 `2` 로 넣거나 `setup --video --openrouter-key-file 경로`, 또는 `OPENROUTER_API_KEY` · `FRAME_VIDEO_KEY_FILE`. 괄호 안에 키 파일이 키 모양이 아니라고 나오면 그 파일을 고친다 |
| `run --video`: `ffmpeg 가 없습니다` | 저장소는 `setup --video` 를 한 번 실행. 번들은 ffmpeg 를 설치해 PATH 에 둔다 (0절 4) |
| `run --video`: `FRAME_VIDEO_FFMPEG 에 지정한 실행 파일이 없습니다` | 그 변수를 지정했으면 서버는 그 파일만 본다 (PATH 로 넘어가지 않는다). 경로를 고치거나 변수를 지운다 |
| `keys --check`: `[거절됨] 키가 거절됐습니다 (HTTP 401)` | 키가 틀렸거나 지워졌다. 서비스 사이트에서 새로 만들어 `keys` 로 다시 넣는다 |
| `keys --check`: `[확인 못 함] 연결하지 못했습니다` | 인터넷 연결을 확인한다. 회사망이면 아래 프록시 설정 |
| 프록시가 있는 회사망 | 런처의 로컬 확인은 프록시를 거치지 않는다. OpenAI · OpenRouter 호출과 `keys --check` 에는 `HTTPS_PROXY` 를 설정 |
| 처음부터 다시 | `run --reset` (DB·산출물 삭제, 키 유지. 실제 영상 모드는 `run --video --reset`). 키까지 지우려면 `.demo` 폴더 삭제 |

## 9. 파일 위치

| 경로 | 내용 | git |
|---|---|---|
| `.venv/` | 백엔드 가상환경 | 제외 |
| `frontend/dist/` | 빌드된 화면 (`setup` 이 생성) | 제외 |
| `.demo/frame.db`, `.demo/artifacts/` | 시연 DB 와 산출물 | 제외 |
| `.demo/video/frame.db`, `.demo/video/artifacts/` | 실제 영상 모드(`run --video`)의 DB 와 산출물 | 제외 |
| `.demo/openai.key`, `.demo/openrouter.key` | `setup` · `keys` 로 저장한 키 | 제외 |
| `build/frame-bundle/`, `dist/` | 번들 작업 폴더와 결과물 (10절) | 제외 |

직접 실행하고 싶다면 런처가 쓰는 설정은 `src/frame/tools/demo.py` 의 `demo_env()` 에 모여 있다
(`FRAME_STRUCTURER`, `FRAME_LLM_RUNTIME_KEY=true`, `FRAME_JOBS=inline`, SQLite, `FRAME_FRONTEND_DIST=frontend/dist`).
실제 영상 모드가 더하는 값은 `src/frame/tools/demo_video.py` 의 `VIDEO_DEFAULTS` · `FORCED` 와 `generator_timeout()`
(생성 시간 제한 = 셸의 `FRAME_VIDEO_TIMEOUT_S` + 300, 없으면 900)이다.

## 10. 번들 만들기 (관리자용)

- **배포**: `v` 로 시작하는 태그를 올리면 CI(`.github/workflows/bundle.yml`)가 5개 대상을 각 OS 러너에서 만들고,
  풀어서 점검한 뒤 GitHub Release 에 첨부한다.

  ```bash
  git tag v0.1.0-demo
  ```

  ```bash
  git push origin v0.1.0-demo
  ```

- **시험 빌드**: Actions 의 `bundle` 워크플로를 수동 실행(Run workflow)하면 Artifacts 로 받을 수 있다.
  번들 관련 파일을 바꾼 PR 에서도 자동으로 돈다.
- **직접 만들기**: 번들은 그 OS·CPU 에서만 만들 수 있다 (동봉 Python 으로 그 플랫폼용 휠을 설치). Python 3.11.4 이상과 Node.js 18 이상이 필요하다.

  ```bash
  python scripts/frame_bundle.py build
  ```

  ```bash
  python scripts/frame_bundle.py verify dist
  ```

  `build` 는 휴대용 Python(astral-sh/python-build-standalone, `src/frame/tools/bundle.py` 에 버전·SHA-256 고정)을 받아 확인하고,
  화면을 빌드하고, FRAME 과 의존성을 설치한 뒤 `dist/frame-demo-<대상>.zip|tar.gz` 와 `.sha256` 을 만든다.
  `verify` 는 임시 폴더에 풀어 시스템 PATH 만 둔 채(다른 Python·Node 없이) 래퍼와 자동 점검을 돌린다.
- **휴대용 Python 버전을 올릴 때**: `bundle.py` 의 `PBS_RELEASE`·`PYTHON_VERSION` 과 대상별 SHA-256 을 그 릴리스의 `SHA256SUMS` 값으로 함께 바꾼다.
- **알려진 제약**: 코드 서명·Apple 공증이 없어 첫 실행 경고가 나온다 (0절). Windows 는 번들 안 가장 긴 경로를 160자 이하로 유지하도록
  빌드가 검사한다 (현재 142자). Windows ARM64 와 musl(Alpine) 번들은 아직 없다.
