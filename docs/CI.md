# 품질 검사 안내 — 로컬과 CI

무엇을 검사하는지, 로컬에서 어떻게 돌리는지, 걸렸을 때 어떻게 고치는지를 적는다. 결정의 배경은
[ADR-0030](adr/0030-ci-quality-gates-and-baselines.md) 과
[ADR-0032](adr/0032-no-machine-paths-single-source-settings.md)(절대 경로 · 설정값의 출처 · 이름 없는 수)에 있다.

## 1. 한 번에 돌리기

```
pip install -e ".[dev,llm,video]"     # 처음 한 번. video 는 실제 영상 테스트에 쓰는 ffmpeg 를 가져온다
python scripts/check.py               # 포맷 -> 정적 검사 -> 타입 검사 -> 테스트와 커버리지 하한
python scripts/check.py lint types    # 고른 것만
python scripts/check.py --list
```

- CI 의 test 잡이 같은 스크립트를 단계별로 부른다. 로컬에서 통과하면 CI 에서도 같은 것을 본다.
- **worktree 에서 작업할 때**: `scripts/check.py` 는 지금 폴더의 `src` 를 쓰도록 `PYTHONPATH` 를 맞춘다.
  손으로 `python -m frame.…` 을 칠 때는 `PYTHONPATH=src` 를 직접 준다. 가상환경의 편집 설치(`pip install -e`)가 다른 폴더를 가리키고 있으면
  그냥 친 명령은 그 폴더의 코드를 돌린다 (`python -c "import frame; print(frame.__file__)"` 로 확인). pytest 는 설정(`pythonpath = ["src"]`) 덕에 괜찮다.
- **출력을 파일·파이프로 받을 때** (`python scripts/check.py > check.log 2>&1`): 로그는 UTF-8 이다. 셸의 코드 페이지를 따르지 않는다
  (한국어 Windows 의 기본은 cp949 다). 도구들(ruff · mypy · pytest · coverage)도 UTF-8 모드(`PYTHONUTF8=1`)로 돌아 로컬과 CI 가 같은 조건이다.
  콘솔에 바로 찍을 때는 그대로다. PowerShell 에서 파이프로 받은 한글이 깨져 보이면 `[Console]::OutputEncoding = [Text.Encoding]::UTF8` 을 먼저 친다.
  다른 인코딩이 필요하면 `PYTHONIOENCODING` 이나 `PYTHONUTF8` 을 직접 정한다 — 스크립트가 그것을 따르고, 그 인코딩이 못 담는 글자는 `\uXXXX` 로 찍는다.
- 유료 API 는 부르지 않는다. 네트워크도 쓰지 않는다 (3절).

## 2. 검사하는 것

| 검사 | 명령 (scripts/check.py 가 부르는 것) | 걸리면 |
|---|---|---|
| 포맷 | `ruff format --check src tests scripts` | `python -m ruff format src tests scripts` 로 고친다 |
| 정적 검사 | `ruff check src tests scripts` | 4절 |
| 타입 검사 | `mypy` (`src` 전체. `domain` 은 strict 수준) | 형을 적는다. 가드 변수로 좁힌 값은 `isinstance` 를 조건에 직접 쓴다 |
| 구조 규칙 | `pytest tests/architecture` (전체 테스트에 들어 있다) | 5절 |
| 테스트 | `pytest` (slow·gpu 는 기본으로 빠진다) | — |
| 커버리지 하한 | 전체 89% · 제품 코드(tools 제외) 95% · 핵심(domain·application) 97% | 테스트를 더한다. 하한은 `scripts/check.py` 의 `COVERAGE_FLOORS` 에 있고 올리기만 한다 |

CI 에만 있는 것: 같은 테스트를 Python 3.12 와 Windows 에서 한 번 더 (compat 잡), 의존성 취약점 검사 (audit 잡, 실패해도 막지 않는다),
화면 빌드 (frontend 잡), 시연 경로 점검 (demo-smoke 잡), 번들 빌드와 릴리스 (`bundle.yml`).

## 3. 유료 테스트와 테스트 환경

- 기본 `pytest` 는 실제 호출 테스트(`slow`)와 GPU 테스트를 뺀다. 돌릴 때는 `-m slow` 를 준다.
- `tests/live` 의 테스트는 **`FRAME_LIVE_TESTS=1` 을 명시했을 때만** 돈다. 서버를 띄울 때 쓰는 `FRAME_GENERATOR=external_api` 나
  `FRAME_STRUCTURER=llm` 이 셸에 남아 있어도 이 스위치 없이는 부르지 않는다.

  ```
  FRAME_LIVE_TESTS=1 FRAME_STRUCTURER=llm FRAME_LLM_KEY_FILE=<키 파일> python -m pytest tests/live -m slow -s
  ```

  실제 호출은 비용이 든다. 돌리기 전에 그 테스트 파일 머리의 예상 비용을 읽는다.
- 그 밖의 테스트는 셸의 `FRAME_*` 와 `OPENAI_API_KEY`·`OPENROUTER_API_KEY` 를 지운 채로 돌고, 이 기기 밖으로 연결하면 실패한다
  (`tests/conftest.py`). 설정이 필요한 테스트는 `monkeypatch.setenv` 나 `Settings(...)` 로 직접 준다.
- 새 실제 호출 테스트는 `tests/live/` 에 두고 `pytestmark = pytest.mark.slow` 를 단다.
- ffmpeg 가 없으면 실제 영상 테스트는 건너뛴다 (실행 머리말의 `ffmpeg:` 줄에 보인다). CI 는 `FRAME_REQUIRE_FFMPEG=1` 로 돌아 없으면 실패한다.

## 4. 정적 검사에 걸렸을 때

- 고칠 수 있으면 고친다. `python -m ruff check --fix src tests scripts` 가 안전한 것은 스스로 고친다.
- 규칙이 맞지 않는 자리라면 그 줄에 이유를 적은 예외를 단다: `# noqa: S310 - 주소는 설정에서 검증한 것만 온다`.
  이유 없는 `# noqa` 와 규칙 번호 없는 `# noqa` 는 쓰지 않는다.
- 자주 걸리는 것

  | 규칙 | 뜻 | 보통의 처리 |
  |---|---|---|
  | `S101` | src 의 `assert` | 실행 중 확인은 명시적 오류(`raise`)로 쓴다. 형을 좁히려는 것이면 조건문으로 |
  | `S310`·`S607`·`S104` | URL 열기, PATH 의 실행 파일, 0.0.0.0 바인드 | 입력을 검증한 뒤 이유를 적은 noqa |
  | `T201` | `print` | 로그(`logging`)로. 명령줄 도구(`src/frame/tools/`)와 `tests/live/` 는 허용 |
  | `BLE001` | 넓은 `except Exception` | 잡을 예외를 좁힌다. 일부러 넓게 잡는 곳은 이유를 적은 noqa |
  | `C901` | 복잡도 19 초과 | 함수를 나눈다 |
  | `ERA001` | 주석으로 남긴 코드 | 지운다 (git 에 남아 있다) |
  | `ISC004` | 목록 안에서 줄을 넘겨 이어 쓴 문자열 | 괄호로 묶는다 |
  | `PLR2004` | 비교식의 이름 없는 수 (`if status == 429`, `if n > 8`) | 상태 코드는 `http.HTTPStatus`, 상한 · 임계값은 뜻을 적은 이름 붙은 상수로. "둘 미만" 은 `<= 1` 로 적는다. tests 는 예외 (ADR-0032) |

- 파일 단위 예외(`pyproject.toml` 의 `per-file-ignores`)는 기준선이다. 새 파일을 더하지 않는다.

## 5. 구조 규칙에 걸렸을 때

`tests/architecture/` 가 지키는 것과 고치는 방법이다. 규칙의 표는 ADR-0030 §3 에 있다.

| 실패한 테스트 | 뜻 | 고치는 방법 |
|---|---|---|
| `test_dependencies_point_inward` | 바깥 계층을 import 했다 (예: domain → adapters) | 포트를 두고 구현을 어댑터로 옮긴다. 조립은 `config/` 에서 한다 |
| `test_adapters_use_only_ports_and_errors_of_the_application` | 어댑터가 유스케이스·DTO 를 import 했다 | 필요한 타입을 포트로 옮기거나 도메인 타입을 쓴다 |
| `test_the_domain_uses_only_the_standard_library` | 도메인이 외부 패키지를 썼다 | 그 일을 포트 뒤로 보낸다 |
| `test_third_party_packages_stay_in_their_layer` | 외부 패키지를 정해진 자리 밖에서 썼다 | 새 패키지라면 테스트의 `THIRD_PARTY_HOMES` 에 자리를 적는다 (ADR 에 적을 만한 결정이다) |
| `test_adapter_packages_do_not_import_each_other` | 어댑터 패키지가 다른 어댑터 패키지를 import 했다 | 함께 쓰는 코드를 `adapters/shared/` 로 옮긴다 |
| `test_there_are_no_modules_at_the_top_of_adapters` | `adapters/` 바로 아래에 모듈을 뒀다 | 그 개념의 패키지나 `adapters/shared/` 로 옮긴다 |
| `test_removed_paths_do_not_come_back` · `test_nothing_imports_a_removed_path` | 옮긴 모듈의 옛 경로가 다시 생겼거나 그 경로로 import 했다 (옛 브랜치를 가져올 때 생긴다) | 실패 문구가 알려 주는 새 자리로 고친다. 표는 `tests/architecture/test_moved_paths.py` 의 `MOVED` (ADR-0031 3절) |
| `test_the_table_points_at_places_that_exist` | 모듈을 또 옮겼는데 `MOVED` 의 새 자리가 옛 이름이다 | 표의 새 자리를 지금 이름으로 고친다. 모듈을 옮길 때는 그 표에 옛 경로 → 새 자리를 더한다 |
| `test_every_package_says_what_it_is_for` · `…lists_its_public_names` | 패키지 `__init__.py` 에 설명이나 `__all__` 이 없다 | 머리에 "맡는 개념 · 밖에서 쓰는 이름 · 안의 모듈 지도" 를 적고 `__all__` 을 둔다 |
| `test_private_modules_are_not_imported_from_outside_their_package` | `_` 로 시작하는 모듈을 패키지 밖에서 import 했다 | 패키지 `__init__.py` 가 내보내는 이름을 쓴다 |
| `test_modules_stay_small_enough_to_read` | 새 모듈이 500줄을 넘는다 | 개념 단위로 나눈다 (줄 수는 신호이고, 나누는 기준은 개념이다) |
| `test_the_adr_index_lists_every_adr` | ADR 을 더했는데 색인에 없다 | `docs/adr/README.md` 의 표에 한 줄 |
| `test_the_module_map_lists_every_package` | 패키지를 더하거나 옮겼는데 모듈 지도에 없다 | `docs/MODULE_MAP.md` §2 의 표를 고친다 |
| `test_no_machine_bound_paths_are_committed` | 커밋되는 글에 드라이브 문자 경로나 홈 · 설치 · 임시 폴더에서 시작하는 경로가 있다 | 설정 · 상대 경로 · `tmp_path` · 자리표시(`path/to/…`)로 바꾼다. 경로를 거절 · 변환 · 가리는지 시험하는 입력처럼 꼭 있어야 하면 `tests/architecture/test_machine_paths.py` 의 `ALLOWED` 에 이유와 함께 적는다 (ADR-0032) |
| `test_every_allowed_path_is_still_there` | 허용 목록에 적은 줄이 없어졌다 | `ALLOWED` 에서 그 항목을 지운다 |
| `test_arguments_that_come_from_settings_have_no_default_of_their_own` | 조립이 설정에서 넘기는 인자에 생성자가 기본값을 또 갖고 있다 | 생성자의 기본값을 지운다. 값은 설정에만 두고 조립이 넘긴다. 그 클래스를 직접 만드는 테스트는 값을 직접 준다 (ADR-0032) |
| `test_address_and_model_defaults_are_written_only_in_the_config` | 설정의 주소 · 모델 기본값을 `frame/config/` 밖의 코드에 다시 적었다 | 그 값을 인자로 받아 쓴다 |

"이제 … 기준선에서 지운다" 로 실패하면 고친 것이다. `tests/architecture/baselines.py` 에서 그 줄을 지운다.

### 기준선을 더해도 되는 경우
게이트가 생기기 전에 갈라진 브랜치가 dev 에 들어올 때뿐이다. 그 브랜치의 것을 이유와 함께 더하고, 뒤에 줄인다.
새로 쓰는 코드를 위해 기준선에 더하지 않는다.

## 6. 첫 push 뒤 확인할 것

이 게이트들은 로컬(Windows, Python 3.11)에서 만들고 확인했다. GitHub 에서는 PR #36 에서 처음 돌았다 (2026-11-14 밤).
로컬 결과는 dev(#31~#35)를 합친 뒤 2588 통과 · 4 건너뜀 · 4 제외였고, 첫 실행에서 드러난 결함을 고친 뒤에는 2598 통과다.

### 첫 실행의 결과 (PR #36)

| 잡 | 결과 | 본 것 |
|---|---|---|
| `frontend` · `demo-smoke` (세 OS) · bundle 의 `build` 5개 | 통과 | 이 브랜치가 고친 런처 · 번들 코드가 러너에서 처음 돌았다. bundle 의 `ci` · `release` 잡은 태그가 아니라 건너뛴다 |
| `docker` | 통과 | 이미지를 처음 빌드했고 `/health` 가 답했다 |
| `compat (windows, 3.11)` | 통과 | |
| `test` (ubuntu, 3.11) · `compat (ubuntu, 3.12)` | **실패** — 둘 다 같은 8건 (2574 통과 · 10 건너뜀) | 아래 '실패 8건' |
| `audit` | 실패 (머지를 막지 않는다) | 알려진 취약점 17건 / 패키지 2개: `starlette 0.46.2`, 러너의 `setuptools 79.0.1`. 보고서 §12.3 결정 10 |

- **실패 8건**: 클립 정규화 3 · 최종 영상 조립 2 · 그것을 쓰는 통합 테스트 3 이 "…None 프레임 (목표 … 96 프레임)" 으로 실패했다.
  Linux 러너의 ffmpeg 는 7.0.2 다 (imageio-ffmpeg 가 OS 마다 다른 빌드를 가져온다. Windows 는 7.1). 그 판에서는 스트림 복사 출력의 진행 줄에서
  프레임 수가 읽히지 않았다. 테스트가 아니라 제품의 결함이다 — Linux(Docker 이미지 포함)에서 실제 생성의 정규화가 늘 실패한다 (보고서 A-42).
  프레임 수를 패킷 목록의 줄 수로 세도록 고쳤다 (`adapters/shared/media/probe.py` 의 `read_video_packets`).
  지금까지의 워크플로는 `video` extra 를 설치하지 않아 ffmpeg 테스트(약 155개)를 건너뛰었고, 그래서 드러나지 않았다.
- **건너뛴 10개**: Windows 전용 1개와, 한글 글꼴을 찾지 못한 화면 문구 합성 테스트 9개 (`tests/contract/test_overlay_contract.py`).
  `FRAME_REQUIRE_FFMPEG` 는 ffmpeg 만 본다. 글꼴 설치(`fonts-noto-cjk`)는 아직 넣지 않았다 — 보고서 §12.3 결정 11.

### 다음 실행에서 볼 것

1. **Linux 의 두 잡**: 고친 프레임 세기가 Linux 의 ffmpeg 7.0.2 에서 도는 것은 다음 실행이 처음이다. 로컬에서는 Windows 의 7.1 로,
   실제 출력에서 진행 줄의 `frame=` 을 지운 조건으로 확인했다. 실패했던 8개 테스트의 뒤쪽 단언(크기 · fps · 길이)도 Linux 에서 처음 돈다.
2. **커버리지 하한**: 첫 실행은 테스트가 실패해 Linux 의 값을 재지 못했다. 하한(89 · 95 · 97)은 Windows 에서 잰 값(94.28 · 96.60 · 97.84)에
   여유를 둔 것이고, 핵심(domain·application)의 여유가 0.84 로 가장 작다. 문구 합성 테스트 9개가 빠지는 만큼 Linux 의 값은 더 낮을 수 있다.
   하한 아래면 `scripts/check.py` 의 `COVERAGE_FLOORS` 를 그 값에 맞추고, 위면 올린다.
3. **docker 잡**: 한 번 통과했다. 몇 번 더 통과하는 것을 보고 `continue-on-error` 를 뺀다.
   실패하면 로그의 `docker build` 단계부터 본다 (비 root 사용자의 쓰기 권한, extra 설치).
4. **audit 잡**: 결과만 본다 (보고서 §12.3 결정 10). 조용해지면 `continue-on-error` 를 뺀다.
5. **릴리스**: 아직 돌려 보지 못했다. `v` 태그를 올리면 `bundle.yml` 의 `ci` 잡이 `ci.yml` 을 부르고, 통과해야 release 가 돈다.
   태그는 패키지 버전으로 시작해야 한다 (버전 0.1.0 → `v0.1.0`, `v0.1.0-demo`).

## 7. 저장소 설정 제안 (이 작업에서는 바꾸지 않았다)

- `dev` 브랜치 보호: 필수 검사 `test`, `frontend`, `demo-smoke (ubuntu-latest)`. 머지 전 최신 상태 요구. `compat` 는 안정된 뒤에 더한다.
- 기본 브랜치를 `dev` 로: 지금 `main` 에는 워크플로 파일이 없어 수동 실행(workflow_dispatch)을 쓸 수 없다.
- `v*` 태그 보호: 릴리스 태그를 아무 커밋에나 달 수 없게.
- 같은 저장소의 `feature-*` 브랜치는 push 와 pull_request 가 둘 다 돌아 잡이 두 배다. push 를 `main`·`dev` 로 줄이면 반으로 준다
  (PR 을 열지 않은 브랜치는 CI 가 돌지 않게 되므로 팀의 흐름에 맞는지 정해야 한다).
- 의존성의 정확한 버전을 잠금 파일로 고정하면 CI·Docker·번들이 같은 조합을 쓴다 (도구 설치가 필요해 아직 정하지 않았다).
