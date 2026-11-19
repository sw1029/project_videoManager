# ADR-0037 시연 실행기의 키 도우미와 실제 영상 모드: OpenRouter 키는 키 파일로, 실제 영상은 `run --video` 로

- 상태: 채택 (2026-11-19)
- 관련: ADR-0010 (시연용 화면 키 입력과 OS 공통 실행 런처), ADR-0012 (생성 시간 제한), ADR-0013 (재시작 복구와 생성 결과 캐시),
  ADR-0035 (인물 일관성 기본값), docs/DEMO.md 4 · 5절

## 맥락
- 새 기기에서 `setup` · `run` 으로 띄우는 시연 실행기(ADR-0010)는 OpenAI 키만 다뤘다 (setup 입력 · 화면 칸 · 환경 변수).
  영상 생성기는 정하지 않아 기본값 `fake` 였다. 그래서 sh / cmd 흐름으로는 실제 영상을 만들 수 없었다.
- OpenRouter 키는 서버가 기동할 때 `OPENROUTER_API_KEY` → `FRAME_VIDEO_KEY_FILE` 순으로 한 번 읽는다. 화면 입력은 없다.
- 셸에서 `FRAME_GENERATOR=external_api` 를 정해도 쓸 수 있는 경로가 아니었다: 실행기가 `FRAME_JOBS=inline` 을 강제해 생성 요청
  하나가 몇 분씩 묶였고(480p 4초 장면 실측 약 2분), `setup` 이 `.[llm]` 만 깔아 동봉 ffmpeg 가 없었다.
- 기동 복구(ADR-0013)는 QUEUED · STRUCTURING 버전을 다시 디스패치하고 PROCESSING 버전을 다시 시도한다. 가짜 생성기로 쓰던
  DB 를 그대로 실제 생성기로 띄우면 거기 남아 있던 작업이 유료로 돈다.
- 사용자 결정 (2026-11-19): OpenRouter 키는 **setup · 키 파일** 방식으로 받고(화면 입력은 하지 않는다), API 설정을 돕는
  안내 CLI 나 실행 파일을 더한다.

## 결정
1. **키 도우미 `keys`** (`frame/tools/demo_keys.py` 키 파일 · 상태 · 온라인 확인, `demo_key_guide.py` 대화형 메뉴 ·
   명령 — 표준 라이브러리만, 의존성 설치 전에도 돈다)
   - 터미널에서 옵션 없이 부르면 메뉴다: `1` OpenAI 키 · `2` OpenRouter 키 · `c` 확인 · `d` 지우기 · `q` 끝내기.
     키는 `getpass` 로 보이지 않게 받는다. 다른 서비스의 키를 잘못된 자리에 넣으면(OpenAI 자리에 `sk-or-...`) 받지 않고 어디에
     넣을지 알린다. 끝낼 때 다음에 할 일(`run`, `run --video`, 준비할 것)을 보인다.
   - 옵션: `--status` · `--check` · `--openai-file` · `--openrouter-file` · `--remove`. 터미널이 아니면(CI · 파이프) 옵션이 없어도
     묻지 않고 상태만 보인다 — 멈추지 않는다.
   - 키 파일은 `.demo/openai.key` · `.demo/openrouter.key` (POSIX 0600, Windows 는 사용자 폴더 권한 — `setup` 과 같은 함수).
     상태는 실행기가 띄운 서버가 쓸 키를 그 순서대로 알린다: 환경 변수(`OPENAI_API_KEY` · `OPENROUTER_API_KEY`) > `.demo`
     키 파일 > 셸의 `FRAME_LLM_KEY_FILE` · `FRAME_VIDEO_KEY_FILE` 의 파일 (OpenAI 는 서버에서 화면 입력이 가장 먼저다).
     실행기는 `.demo` 의 키 파일에 그 서비스의 키가 있을 때만 서버에 넘긴다 — 아니면 셸의 키 파일 설정을 덮지 않는다.
     서버가 읽을 파일은 **서버와 같은 규칙**으로 읽는다: 키 모양(`OPENAI_KEY_TOKEN` · `OPENROUTER_KEY_TOKEN`)을 공용 모듈
     `frame.adapters.shared.api_keys` 로 옮겨 서버의 어댑터와 도우미가 함께 쓴다 (서버는 OpenAI 키 파일에서도 sk-or- 키를 읽는다
     — 그러면 도우미도 그 키를 알리고 'OpenAI 주소로는 쓰이지 않는다' 고 덧붙인다). 입력받을 때는 더 엄하다 (OpenAI 자리에
     sk-or- 를 받지 않는다). 경로 설정은 서버(`Settings`)처럼 다듬지 않고 그대로 보며, 없는 경로 · 키 없는 파일 · 경로 대신
     든 키를 따로 알린다. 화면 · 터미널에는 끝 네 글자만 보인다 (ADR-0010) — 경로 자리에 든 키도 가린다.
   - **온라인 확인**은 돈이 들지 않는 조회 하나다: OpenAI `GET /models`, OpenRouter `GET /key`(그 키로 쓴 금액 · 남은 한도).
     키는 **서버가 그 키를 보낼 주소**로만 보낸다. 주소는 실행기에 다시 적지 않고 설치된 서버 코드에 묻는다
     (`ADDRESS_PROBE` 를 서버의 Python — 번들의 python 또는 `.venv` — 으로 돌려 `Settings` 의 기본값과
     `FRAME_LLM_BASE_URL` · `FRAME_VIDEO_BASE_URL` 을 그대로 받는다, ADR-0032 의 '값은 한 곳에'). 그래서 확인은
     `setup` 뒤에 되고, 키를 넣고 보는 것은 설치 전에도 된다.
     보내는 길은 어댑터와 같은 공용 전송(`frame.adapters.shared.http` 의 `UrllibTransport` · `secure_base_url`)이다:
     https(또는 이 기기의 로컬 서버)로만, 키 헤더는 리다이렉트로 넘기지 않고, 헤더에 쓸 수 없는 값은 문구에 싣지 않는다.
     OpenAI 주소로 가는 sk-or- 키(서버의 `_goes_to_openai` 와 같은 판정 — 서버도 보내지 않는다), OpenRouter 자리의 OpenAI 키,
     공백 · 줄바꿈이 섞인 키는 보내지 않고 '거절됨'. `FRAME_LLM_BASE_URL` 을 OpenRouter 로 돌렸으면 서버는 OpenAI 자리의
     sk-or- 키를 쓰므로 확인도 그 주소로 보낸다. 401 · 403 은 '거절됨'(종료 코드 1)이되, 모델 목록 권한만 없는(권한을 줄인) OpenAI 키는
     '확인 못 함' 이다 — 분석에는 쓸 수 있다. 429 · 연결 실패도 '확인 못 함'. 확인 중 Ctrl+C 는 메뉴로 돌아간다.
   - **실행 파일**: `scripts/keys.cmd`(탐색기에서 더블클릭), `scripts/keys.sh`, 번들의 `keys.cmd` · `keys.sh`.
     키를 묻는 `.cmd`(`keys` · `setup`)는 **탐색기의 더블클릭으로 열렸을 때만** 끝에서 멈춰 마지막 안내를 읽게 한다.
     판별: 따옴표를 뺀 `%cmdcmdline%` 이 `%ComSpec% /c <이 파일의 전체 경로> ` 와 같을 때 (탐색기가 여는 꼴). 명령줄은 따옴표
     안에서만 펼쳐 경로의 `&` 가 줄을 가르지 않는다. 터미널 · PowerShell · `cmd /c "... 인자"` 로 부르면 멈추지 않는다.
     `run.cmd` 는 멈추지 않는다 (서버를 띄우고, 번들 확인이 `run.cmd --help` 를 부른다).
2. **실제 영상 모드 `run --video`** (`frame/tools/demo_video.py`)
   - 강제하는 값: `FRAME_GENERATOR=external_api`, `FRAME_ASSEMBLER=ffmpeg`, `FRAME_JOBS=thread` (화면이 진행 상태를 묻는다).
   - 셸에서 정하지 않았을 때의 값: 해상도 `480p`(720p 의 절반 요금), 검수기 `vlm_requirement,vlm_consistency,ssim_preservation`
     (실제 영상에 가짜 검수기는 늘 통과라 뜻이 없다), `FRAME_GENERATOR_TIMEOUT_S` = 셸의 `FRAME_VIDEO_TIMEOUT_S` + 300
     (없으면 900 — 영상 대기 기본 600초보다 길게, ADR-0012).
   - **데이터는 `.demo/video/` 에 따로** 둔다 — 기동 복구가 가짜 모드의 남은 작업을 유료 생성기로 돌리지 않게. `--reset` 도 그 폴더만.
   - 띄우기 전에 OpenRouter 키(위의 상태 순서)와 ffmpeg(서버의 `resolve_ffmpeg` 와 같은 순서: `FRAME_VIDEO_FFMPEG` → PATH →
     imageio-ffmpeg. `FRAME_VIDEO_FFMPEG` 를 정했으면 서버는 그 파일만 본다 — 상대 경로는 서버가 뜨는 폴더 기준)를 보고,
     없으면 할 일을 알리고 멈춘다. `--offline` 과는 함께 쓸 수 없다.
   - `.demo/openrouter.key` 에 키가 있을 때만 `FRAME_VIDEO_KEY_FILE` 로 넘긴다 (없으면 서버가 `OPENROUTER_API_KEY` 나 셸의
     `FRAME_VIDEO_KEY_FILE` 을 그대로 본다).
3. **`setup --video`**: `pip install -e ".[llm,video]"`(imageio-ffmpeg)로 ffmpeg 까지 깔고 OpenRouter 키를 묻는다
   (`--openrouter-key-file` 로 파일을 줄 수도 있다). 그냥 `setup` 은 지금처럼 가볍다 (`.[llm]`).
4. **`doctor`** 가 두 키의 상태, 키 도우미 명령, 실제 영상 모드가 준비됐는지를 보인다.
5. **번들**에는 키 도우미 래퍼와 안내문을 더하고, **ffmpeg 는 넣지 않는다**. imageio-ffmpeg 의 Windows 실행 파일은 GPLv3 빌드
   (gyan.dev, `--enable-gpl --enable-version3`, 87MB)라 넣으면 라이선스 고지 · 소스 안내가 필요하고 번들(지금 약 28MB)이 몇 배 커진다.
   번들의 실제 영상 모드는 사용자가 설치한 ffmpeg(PATH)를 쓴다 — 안내문에 OS 별 설치 명령을 적는다.
6. CI 의 `demo-smoke` 가 세 OS 에서 `keys --status` 를 돌린다 (터미널 없이 멈추지 않고 끝나는지).

## 결과
- 새 기기의 흐름: `setup`(실제 영상까지면 `setup --video`) → `keys`(필요하면) → `run`(또는 `run --video`). 키 파일이 있으면 자동으로 쓴다.
- 요금: 키 확인은 무료 조회다. 실제 영상은 생성 요청 때만 든다 (480p 4초 장면 실측 약 $0.15, 2026-11-19).
- 한계
  - OpenRouter 키는 화면에서 넣지 않는다. 서버가 기동할 때 읽으므로 바꾸면 서버를 다시 띄운다.
  - 키 확인은 키가 통하는지만 본다. 계정 잔액이 모자라 생성이 거절되는 것은 생성할 때 알게 된다 (OpenRouter 는 키의 한도는 보여 준다).
  - 더블클릭 판별은 탐색기가 `.cmd` 를 여는 꼴(`%ComSpec% /c "<경로>"`)에 기댄다. 다른 프로그램이 똑같은 꼴로 열면 끝에서 한 번
    멈춘다 (입력이 없으면 바로 지나간다).
  - 저장한 키는 서버를 시작할 때 읽는다. 실행 중에 `keys` 로 저장하면 서버를 다시 띄워야 쓰인다 (안내문 · 번들 README 에 적는다).
  - 병합 전 검토(2026-11-19, 네 관점 + 반박 검증)에서 확인된 21건과 추가 2건을 이 결정에 반영했다: 셸의 키 파일 변수,
    다른 서비스 키 · 줄바꿈 키를 보내지 않기, 리다이렉트로 키 헤더가 넘어가지 않는 공용 전송, 권한을 줄인 키, `FRAME_VIDEO_FFMPEG`
    판정, 시간 제한 기본값, 더블클릭 판별, Ctrl+C, 번들 안내, 문서와 테스트. 고친 뒤의 델타 검토(두 관점 + 반박 검증 2표)에서
    나온 7건도 반영했다: 경로 자리에 든 키 가리기, ffmpeg 확인의 `-X utf8`, 서버와 같은 키 파일 규칙(공용 키 모양),
    OpenAI 주소일 때만 sk-or- 막기, 없는 경로와 키 없는 파일 가르기, 시간 제한 문서.
  - 서버 쪽의 같은 종류 문구(키 파일이 없을 때 서버가 그 경로를 그대로 보이는 것)는 이 결정 밖이다 — 실행기는 그 전에 멈춘다.
  - 키 파일은 평문이다 (ADR-0010 과 같다. 사용자 프로필로 옮기는 것은 감사 §12.3 결정 8).

## 고른 것과 버린 것
- 화면의 OpenRouter 키 칸: 생성기가 기동 때 키를 읽어, 바꾸려면 LLM 처럼 키 저장소 구조가 필요하다. 유료 생성 키를 화면에서
  넣게 하는 위험도 있어 이번에는 하지 않는다 (사용자 결정).
- PyInstaller 로 만든 키 설정 exe: 빌드 도구 · 백신 오탐 · OS 별 빌드가 필요하다. `.cmd` 더블클릭으로 같은 일을 한다.
- 실제 영상 모드가 가짜 모드와 같은 `.demo` DB 를 쓰기: 기동 복구가 남은 작업을 유료로 돌릴 수 있어 버린다.
- 실제 영상 모드에서 추적기(`FRAME_TRACKER=opencv`)까지 켜기: tracking extra(OpenCV 약 60MB)가 필요하고 화면 오버레이가 아직 없어
  기본으로 켜지 않는다. 쓰려면 셸에서 `FRAME_TRACKER=opencv` 를 정하고 `pip install -e ".[tracking]"`.
