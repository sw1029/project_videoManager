# ADR-0034 Docker 이미지는 배포판의 ffmpeg 와 한글 글꼴을 쓰고, CI 가 이미지 안에서 실제 영상 경로를 돌린다

- 상태: 채택 (2026-11-15)
- 관련: 백엔드 감사 보고서(`docs/audit/2026-11-backend-audit.md` A-42 · A-43 · §12.3 결정 12), ADR-0027 (화면 문구 얹기),
  ADR-0030 (CI 품질 게이트 — docker 잡), 이슈 #6 (Docker 이미지)

## 맥락
- 영상 처리는 모두 ffmpeg 하나로 한다: 클립 정규화(프레임 세기) · 최종 영상 조립 · 프레임 뽑기 · 소리 편집 · 영상 분석 · 화면 문구 합성.
  찾는 순서는 `FRAME_VIDEO_FFMPEG` → PATH 의 ffmpeg → video extra 의 번들(imageio-ffmpeg)이다.
- 이미지는 지금까지 `.[llm,video]` 로 설치해 번들을 썼다. 그 번들이 Linux 에 가져오는 ffmpeg(7.0.2 정적 빌드)에는 글자를 그리는
  `drawtext` 필터가 없다 (A-43). 이미지에는 한글 글꼴도 없다. 그래서 이미지에서 `FRAME_OVERLAY_RENDERER=ffmpeg` 를 켜면 기동할 때 거절했다.
- CI 의 docker 잡은 이미지를 빌드해 `/health` 만 봤다. 이미지 안의 ffmpeg 로 영상 경로가 도는지는 본 적이 없다.
  ffmpeg 는 판 · 빌드마다 다르게 군다 — A-42(진행 줄에서 프레임 수가 읽히지 않는다)와 A-43 이 둘 다 Linux 의 빌드에서만 났다.
- 사용자 결정 (2026-11-15, 보고서 §12.3 결정 12): 이미지에 시스템 ffmpeg 와 한글 글꼴을 넣는다. 이미지 안에서 실제 영상 경로를 돌리는 점검을
  CI 에 함께 넣는다.

## 결정

### 1. 이미지의 ffmpeg 는 배포판의 것 하나다
- `apt-get install --no-install-recommends ffmpeg fonts-nanum` 으로 넣는다. video extra 는 이미지에 설치하지 않는다 (`.[llm]`).
  PATH 의 ffmpeg 가 번들보다 먼저 쓰이므로 번들은 넣어도 쓰이지 않는다 (약 80MB). 하나만 두면 "이미지가 어느 ffmpeg 로 도는가" 의 답이 하나다.
- 그 결과 이미지의 영상 경로는 **전부** 배포판의 ffmpeg 로 돈다. 화면 문구만이 아니다.
- 바탕 이미지를 `python:3.11-slim-trixie` 로 적는다. 지금의 `python:3.11-slim` 과 같은 이미지다 (2026-11-15 에 Docker Hub 에서 본 두 태그의
  amd64 다이제스트가 같다). 배포판의 이름이 없는 `slim` 태그는 다음 Debian 이 나오면 그쪽으로 옮겨 가고, 그때 ffmpeg 의 판이 커밋 없이 바뀐다.
  trixie(Debian 13)의 ffmpeg 는 7.1.5 다 (`7:7.1.5-0+deb13u1`).
- 꾸러미의 판은 정확히 고정하지 않는다 (`ffmpeg=7:7.1.5-…` 로 적지 않는다). Debian 은 보안 갱신이 나오면 옛 판을 저장소에서 내려,
  정확히 고정한 빌드는 그날부터 깨진다. 안정판 안에서는 7.1.x 의 고친 판만 온다. 어느 판으로 빌드됐는지는 CI 의 로그에 찍힌다 (3절).

### 2. 한글 글꼴은 fonts-nanum
- 나눔 글꼴 꾸러미다 (TTF 12개, 설치 크기 32.5 MiB). 글꼴을 지정하지 않았을 때 찾는 이름(`KOREAN_FONT_FILES`)에 `NanumGothicBold.ttf` 가 있고,
  Noto Sans CJK 보다 먼저 고른다.
- `fonts-noto-cjk` 는 89 MiB 다. 화면 문구는 한국어이고, 다른 글꼴이 필요하면 파일을 붙여 `FRAME_OVERLAY_FONT` 로 지정한다.
- CI 에서 두 조합이 따로 돈다: overlay-font 잡은 Ubuntu 의 ffmpeg 와 Noto Sans CJK(.ttc), docker 잡은 Debian 의 ffmpeg 와 나눔(.ttf).

### 3. CI 의 docker 잡이 이미지 안에서 돌린다
`/health` 확인 뒤에 세 단계를 더한다.

| 단계 | 하는 일 | 실패하는 때 |
|---|---|---|
| 크기와 판 | `docker image ls` · `docker history`(층마다의 크기), 배포판의 이름, `dpkg-query -W ffmpeg fonts-nanum`, `ffmpeg -version`, 필터 목록의 drawtext 줄 | drawtext 가 없거나 꾸러미가 없다 |
| 이미지 그대로 조립 | `FRAME_OVERLAY_RENDERER=ffmpeg` · `FRAME_AUDIO_EDITOR=ffmpeg` · `FRAME_THEME_MEDIA=ffmpeg` 로 `create_default_app()` | 렌더러가 글꼴이나 drawtext 를 찾지 못한다 (만들어질 때 확인한다) |
| 이미지 안 전체 테스트 | 저장소를 읽기 전용으로 붙여 베끼고, 이미지의 패키지 위에 dev extra 만 더한 가상 환경에서 `python -m pytest` | 테스트가 실패한다. ffmpeg · drawtext · 한글 글꼴을 찾지 못하면 테스트를 모으기 전에 실패한다 (`FRAME_REQUIRE_FFMPEG` · `FRAME_REQUIRE_KOREAN_FONT` · `FRAME_REQUIRE_DRAWTEXT`) |

- **전체 테스트를 돌리는 까닭**: 영상 경로를 쓰는 테스트를 목록으로 따로 관리하지 않기 위해서다. 새 영상 테스트는 저절로 들어온다.
  A-42 의 프레임 세기(패킷 목록의 줄 수) · 정규화 · 조립과 화면 문구 합성 테스트가 여기서 배포판의 ffmpeg 로 돈다.
  다른 Linux 잡(test · compat)은 번들 7.0.2 로 돌고 화면 문구 합성 테스트는 건너뛴다.
- **사용자를 바꾸지 않는다**: 이미지가 정한 사용자(서버가 도는 `frame`. root 가 아니다)로 돈다. 그래서 개발 도구는 시스템이 아니라
  `--system-site-packages` 가상 환경에 깐다. 가상 환경과 저장소 사본은 `mktemp -d` 로 만든 폴더에 둔다.
  테스트는 셸의 `FRAME_*` 를 지운 채로 돌므로 (`tests/conftest.py`) 이미지가 정한 서버 설정(`FRAME_PERSISTENCE` 등)은 테스트에 새지 않는다.
- **머지를 막지 않는다** (`continue-on-error`). 이 기기에서는 이미지를 만들어 보지 못했다 (Docker 데몬이 꺼져 있다). 첫 실행을 본 뒤에 정한다.
  잡의 시간 제한은 15분에서 25분으로 늘렸다.
- 이 단계들이 워크플로에 있는지는 `tests/unit/test_docker_files.py` 가 글로 읽어 확인한다 (docker 를 띄우지 않는다).

### 4. 글꼴 찾기는 볼 수 없는 폴더를 건너뛴다
- `find_font` 가 글꼴 폴더가 있는지 묻는 것(`is_dir`)도 `OSError` 를 건너뛰는 범위 안에 둔다. 홈 폴더를 읽을 권한이 없는 사용자로 돌면
  (컨테이너에서 사용자만 바꿔 돌려 HOME 이 다른 사용자의 것으로 남는 경우) 그 물음이 `PermissionError` 를 내, 다른 폴더에 글꼴이 있어도
  기동이 멈췄다.

## 결과
- 이미지에서 `FRAME_OVERLAY_RENDERER=ffmpeg` 를 켤 수 있다. 기본값은 그대로 `fake` 다.
- **이미지가 커진다. 어림으로 약 +400MB.** Debian trixie 의 공개 꾸러미 색인(main · amd64)에서 `--no-install-recommends` 로 따라오는 것을 셌다:
  꾸러미 205개, 설치 크기 477 MiB (ffmpeg 와 그것이 요구하는 것 204개가 444 MiB, 글꼴 32.5 MiB), 내려받는 크기 143 MB. 번들이 빠져 약 80MB 가 준다.
  444 MiB 가운데 192 MiB 는 그래픽 드라이버다 (libllvm19 124 · mesa-libgallium 41 · libz3 27). ffmpeg 꾸러미가 SDL 을 요구해서 들어오고
  (libsdl2 → libgbm1 → mesa-libgallium → libllvm19 → libz3), 서버는 쓰지 않는다. 바탕 이미지에 이미 든 것은 어림으로 뺐다.
  정확한 값은 docker 잡의 `docker history` 에 찍힌다.
- CI 가 도는 ffmpeg 가 넷이 된다: Linux 의 번들 7.0.2 (test · compat), Windows 의 번들 7.1 (compat), Ubuntu 의 것 (overlay-font),
  Debian 의 7.1.5 (docker).
- 이미지 밖은 그대로다. 로컬 · 번들 · 시연 런처는 전처럼 video extra 의 ffmpeg 를 쓰고, Linux 에서 화면 문구를 쓰려면 drawtext 가 든 ffmpeg 를
  PATH 에 두거나 `FRAME_VIDEO_FFMPEG` 로 지정한다 (ADR-0027 의 보완).
- 확인하지 못한 것: 이미지 빌드와 세 단계는 CI 의 다음 실행이 처음이다. 워크플로의 문법도 눈으로만 봤다.
- 올릴 때 (다음 Debian · 다음 Python): Dockerfile 의 태그를 바꾸고, docker 잡이 찍는 ffmpeg 의 판과 이미지 안 테스트의 결과를 본다.

## 대안
- **drawtext 가 든 정적 빌드를 내려받아 넣기**: 가장 작다 (약 80MB). 하지만 배포판 밖의 빌드를 이미지 빌드 때 내려받게 된다 —
  출처를 고르고, 체크섬으로 고정하고, 보안 갱신을 따라가는 일을 이 저장소가 맡는다.
- **이미지 빌드에서 ffmpeg 를 직접 컴파일** (필요한 라이브러리만): 작고 판을 정확히 고정할 수 있지만 빌드가 길어지고 유지할 것이 는다.
- **화면 문구만 쓰는 ffmpeg 를 따로 지정하는 설정**, **libass 로 그리는 렌더러**, **그대로 두기**: 결정 12 의 (나) · (다) · (라). 사용자가 (가)를 골랐다.
- **이미지 안 점검을 계약 테스트 몇 파일로 한정**: 빠르지만 목록을 관리해야 하고 새 영상 경로가 빠진다. 전체는 몇 분 더 걸릴 뿐이다.
- **root 로 개발 도구를 깔고 사용자를 바꿔 돌리기**: 이미지가 정한 사용자와 다른 조건이 섞인다 (HOME, 파일의 소유자). 가상 환경이 더 단순하다.
