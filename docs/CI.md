# 품질 검사 안내 — 로컬과 CI

무엇을 검사하는지, 로컬에서 어떻게 돌리는지, 걸렸을 때 어떻게 고치는지를 적는다. 결정의 배경은
[ADR-0030](adr/0030-ci-quality-gates-and-baselines.md) 에 있다.

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
| `test_every_package_says_what_it_is_for` · `…lists_its_public_names` | 패키지 `__init__.py` 에 설명이나 `__all__` 이 없다 | 머리에 "맡는 개념 · 밖에서 쓰는 이름 · 안의 모듈 지도" 를 적고 `__all__` 을 둔다 |
| `test_private_modules_are_not_imported_from_outside_their_package` | `_` 로 시작하는 모듈을 패키지 밖에서 import 했다 | 패키지 `__init__.py` 가 내보내는 이름을 쓴다 |
| `test_modules_stay_small_enough_to_read` | 새 모듈이 500줄을 넘는다 | 개념 단위로 나눈다 (줄 수는 신호이고, 나누는 기준은 개념이다) |
| `test_the_adr_index_lists_every_adr` | ADR 을 더했는데 색인에 없다 | `docs/adr/README.md` 의 표에 한 줄 |
| `test_the_module_map_lists_every_package` | 패키지를 더하거나 옮겼는데 모듈 지도에 없다 | `docs/MODULE_MAP.md` §2 의 표를 고친다 |

"이제 … 기준선에서 지운다" 로 실패하면 고친 것이다. `tests/architecture/baselines.py` 에서 그 줄을 지운다.

### 기준선을 더해도 되는 경우
게이트가 생기기 전에 갈라진 브랜치가 dev 에 들어올 때뿐이다. 그 브랜치의 것을 이유와 함께 더하고, 뒤에 줄인다.
새로 쓰는 코드를 위해 기준선에 더하지 않는다.

## 6. 첫 push 뒤 확인할 것

이 게이트들은 로컬에서만 확인했다 (워크플로를 GitHub 에서 돌려 보지 못했다). 처음 돌 때 아래를 본다.

1. **test 잡의 실제 영상 테스트**: ffmpeg 를 쓰는 테스트 약 94개가 CI 에서 처음 돈다. 실행 머리말의 `ffmpeg:` 줄에 경로가 찍히는지,
   건너뛴 수가 3 안팎인지 본다. Linux 에서만 실패하는 것이 있으면 그 테스트가 드러낸 문제다.
2. **커버리지 하한**: 하한은 Windows 에서 잰 값(90.3 · 95.6 · 97.7)에 여유를 둔 것이다. ubuntu 의 값이 하한 아래면
   `scripts/check.py` 의 `COVERAGE_FLOORS` 를 그 값에 맞추고, 위면 올린다.
3. **compat 잡**: Python 3.12(ubuntu)와 Windows 러너에서 같은 테스트가 처음 돈다.
4. **audit 잡**: 결과만 본다. 고칠 것이 있으면 이슈로 올리고, 조용해지면 `continue-on-error` 를 뺀다.
5. **릴리스**: `v` 태그를 올리면 `bundle.yml` 의 `ci` 잡이 `ci.yml` 을 부르고, 통과해야 release 가 돈다. 태그는 패키지 버전으로
   시작해야 한다 (버전 0.1.0 → `v0.1.0`, `v0.1.0-demo`).

## 7. 저장소 설정 제안 (이 작업에서는 바꾸지 않았다)

- `dev` 브랜치 보호: 필수 검사 `test`, `frontend`, `demo-smoke (ubuntu-latest)`. 머지 전 최신 상태 요구. `compat` 는 안정된 뒤에 더한다.
- 기본 브랜치를 `dev` 로: 지금 `main` 에는 워크플로 파일이 없어 수동 실행(workflow_dispatch)을 쓸 수 없다.
- `v*` 태그 보호: 릴리스 태그를 아무 커밋에나 달 수 없게.
- 같은 저장소의 `feature-*` 브랜치는 push 와 pull_request 가 둘 다 돌아 잡이 두 배다. push 를 `main`·`dev` 로 줄이면 반으로 준다
  (PR 을 열지 않은 브랜치는 CI 가 돌지 않게 되므로 팀의 흐름에 맞는지 정해야 한다).
- 의존성의 정확한 버전을 잠금 파일로 고정하면 CI·Docker·번들이 같은 조합을 쓴다 (도구 설치가 필요해 아직 정하지 않았다).
