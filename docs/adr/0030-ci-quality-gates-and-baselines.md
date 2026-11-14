# ADR-0030 CI 품질 게이트와 기준선 방식

- 상태: 채택 (2026-11-14)
- 관련: 백엔드 감사 보고서(`docs/audit/2026-11-backend-audit.md` §9), ADR-0001 (4계층 + Ports & Adapters),
  사용 안내 `docs/CI.md`, 모듈 지도 `docs/MODULE_MAP.md`

## 맥락
- CI 가 지키던 것은 ruff 의 기본 규칙(E·F·I·B·UP)과 pytest 뿐이었다. 포맷, 타입, 계층 규칙, 커버리지, 보안, 문서의 일치는 사람이 지켰다.
  감사 시점에 계층 방향 위반은 0건이었지만 그것을 지키는 검사는 없었다.
- CI 는 ffmpeg 를 설치하지 않았다. ffmpeg 가 없으면 실제 어댑터 테스트(VLM 검수기, 정체성 추출, 클립 정규화·조립 — 약 94개)가
  조용히 건너뛰어진다. 그 조건에서 커버리지는 90% 가 아니라 81% 다.
- 기본 `pytest` 는 유료 API 를 부르는 테스트를 수집했고, 그 테스트를 켜는 스위치가 서버 설정과 같은 환경 변수였다.
  실제 생성기로 서버를 띄우던 셸에서 문서대로 `pytest` 를 치면 과금될 수 있었다.
- 릴리스 번들은 태그 push 로 만들어지는데, 태그 push 는 테스트 워크플로를 돌리지 않았다.
- 여러 브랜치가 동시에 진행 중이다. 게이트를 한 번에 엄격하게 걸면 그 브랜치들이 dev 에 들어오지 못한다.

## 결정

### 1. 기준선과 래칫
- 새 게이트는 **지금 코드에서 통과하는 상태로** 들어간다. 지금 걸리는 것은 기준선 목록에 이유와 함께 적는다.
- 목록은 **줄이기만** 한다. 고쳐서 더는 걸리지 않으면 목록에서 지운다 (구조 규칙의 기준선은 지우지 않으면 테스트가 실패한다).
- 새로 쓰거나 고친 코드는 처음부터 규칙을 지킨다.
- 예외는 하나다: 게이트가 생기기 전에 갈라진 브랜치가 dev 에 들어올 때는 그 브랜치의 것을 이유와 함께 목록에 더할 수 있다.
- 기준선이 있는 곳

  | 게이트 | 기준선의 자리 |
  |---|---|
  | 포맷 | `pyproject.toml` `[tool.ruff.format] exclude` |
  | ruff 규칙 | `pyproject.toml` `[tool.ruff.lint.per-file-ignores]`, 줄 끝의 `# noqa: 규칙 - 이유` |
  | 타입 | `pyproject.toml` `[[tool.mypy.overrides]]` |
  | 구조 규칙 | `tests/architecture/baselines.py` |
  | 커버리지 | `scripts/check.py` `COVERAGE_FLOORS` (하한. 올리기만 한다) |

### 2. 게이트

| 게이트 | 무엇을 지키는가 | 켤 때의 상태 |
|---|---|---|
| 포맷 | `ruff format --check src tests scripts` | 6파일을 포맷했다. 다른 브랜치가 고치고 있는 2파일은 기준선 |
| 정적 검사 | ruff 규칙 — 기본 5군 + 15군(위반 0) + 6군(8건 수정) + 보안 `S` · print `T20` · 복잡도 `C90` | `S` 는 11곳에 이유를 적은 noqa, src 의 assert 9파일은 기준선. `S603` 은 끈다 (모든 subprocess 호출에 걸려 신호가 없다). 복잡도 상한 19 |
| 타입 | `mypy` — `src` 전체는 기본 설정, `domain` 은 strict 가 켜는 검사 | 24건 가운데 21건을 고쳤다. 다른 브랜치가 고치고 있는 2모듈의 3건은 기준선 |
| 구조 규칙 | pytest (`tests/architecture/`) — 계층 방향, 어댑터 경계, 패키지의 입구, 모듈 크기 | 계층 위반 0 (예외 목록 없음). 설명 없는 패키지 10 · `__all__` 없는 패키지 8 · 500줄 넘는 모듈 4 · `adapters/shared/` 밖의 공용 모듈 4 는 기준선 |
| 문서 | pytest — ADR 색인(`docs/adr/README.md`)과 모듈 지도(`docs/MODULE_MAP.md`)가 실제 파일과 같다 | 두 문서를 새로 썼다 |
| 테스트 | pytest. CI 는 ffmpeg 를 설치하고 없으면 실패한다 (`FRAME_REQUIRE_FFMPEG=1`) | 1011 통과 |
| 커버리지 하한 | 전체 89% · 제품 코드(tools 제외) 95% · 핵심(domain·application) 97% | 측정값 90.3 · 95.6 · 97.7 (Windows, Python 3.11) |
| 유료 테스트 보호 | 기본 실행에서 slow·gpu 를 뺀다. `tests/live` 는 `FRAME_LIVE_TESTS=1` 일 때만 돈다. 그 밖의 테스트는 셸의 `FRAME_*`·API 키를 지우고 루프백 밖으로 연결하지 못한다 | — |
| 의존성 취약점 | `pip-audit` (CI 잡 안에서만 설치) | 처음에는 실패해도 머지를 막지 않는다 |
| 릴리스 | `bundle.yml` 의 release 가 같은 커밋의 `ci` 를 요구한다. 태그는 패키지 버전으로 시작해야 한다 | 아직 태그가 없어 돌아 본 적이 없다 |

### 3. 계층 규칙
`docs/ARCHITECTURE.md` §3.1 의 표를 실제 코드에 맞춰 다시 적는다. 테스트(`tests/architecture/test_layer_rules.py`)가 이 표를 지킨다.

| 계층 | import 해도 되는 것 |
|---|---|
| `domain` | `domain`, 표준 라이브러리 |
| `application` | `domain`, `application` |
| `adapters` | `domain`, `application.ports`, `application.errors`, 그리고 같은 어댑터 패키지와 `adapters.shared` |
| `presentation` | `domain`, `application`, `config`, 패키지 최상위(`frame.__version__`) |
| `config` | `domain`, `application`, `adapters` (조립 지점) |
| `evaluation` | `domain`, `application` |
| `tools` | 모든 계층 |

외부 패키지는 자리가 정해져 있다: fastapi·starlette·pydantic 은 `presentation`, sqlalchemy 는 `adapters.persistence`,
openai 는 `adapters.llm`, imageio_ffmpeg 는 `adapters`. 새 외부 패키지를 쓰려면 테스트의 표에 자리를 적는다.

### 4. 새 도구를 들이지 않는다
- 계층 규칙은 import-linter 대신 pytest 테스트로, 보안 정적 분석은 bandit 대신 ruff 의 `S` 로, 복잡도는 radon 대신 ruff 의 `C90` 으로 한다.
- mypy 는 dev extra 에 이미 있던 도구다. 로컬 공유 가상환경에는 사용자 승인을 받아 설치했다.
- ruff 와 mypy 는 버전에 따라 결과가 달라지므로 dev extra 에서 정확히 고정한다 (ruff 0.17.4, mypy 2.4.1).

### 5. 로컬과 CI 는 같은 입구를 쓴다
- `python scripts/check.py` 가 포맷, 정적 검사, 타입 검사, 테스트와 커버리지 하한을 돌린다. CI 의 test 잡은 이 스크립트를 단계별로 부른다.
- 스크립트는 이 저장소 폴더의 `src` 를 `PYTHONPATH` 에 넣는다. 가상환경의 편집 설치가 다른 worktree 를 가리켜도 지금 폴더의 코드를 검사한다.

## 결과
- 사람이 지키던 규칙(계층, 포맷, 타입, 유료 테스트 스위치)이 CI 에서 실패로 드러난다.
- 실제 어댑터 테스트가 CI 에서 돈다.
- 남은 일과 한계
  - **워크플로를 GitHub 에서 돌려 보지 못했다** (이 작업은 push 하지 않는다). 로컬에서 같은 검사는 통과하고 mypy 는
    Linux·macOS·Python 3.12 대상으로도 통과하지만, 첫 push 뒤 확인할 것을 `docs/CI.md` 에 적었다.
  - 커버리지 하한은 Windows 에서 잰 값에 여유를 둔 것이다. CI(ubuntu)의 실제 값을 본 뒤 맞춘다.
  - 의존성의 정확한 버전(잠금 파일)은 정하지 않았다. 번들과 CI 는 여전히 설치 시점의 최신 버전을 쓴다.
  - GitHub 저장소 설정(브랜치 보호와 필수 검사)은 바꾸지 않았다. 제안은 `docs/CI.md` 에 있다.
  - 액션은 메이저 태그(`@v4`)로만 고정돼 있다. 같은 저장소의 PR 은 push 와 pull_request 가 둘 다 돌아 잡이 두 배다.
  - `docs/ARCHITECTURE.md` §3.1·§12 는 다른 브랜치가 고치고 있어 이 ADR 과 `docs/adr/README.md` 가 먼저 바뀌었다.
    그 브랜치들이 dev 에 들어온 뒤 본문이 이 문서들을 가리키게 한다.

## 대안
- import-linter: 계층 규칙만 보고, 패키지 입구·모듈 크기·문서 일치는 따로 필요하다. 도구를 하나 더 설치해야 한다.
- 처음부터 전부 엄격하게 (기준선 없음): 진행 중인 브랜치가 모두 막힌다.
- 바뀐 줄만 검사 (diff 기준): 설정이 간단하지만 기존 위반이 어디에 얼마나 있는지 보이지 않고 줄어들지도 않는다.
- pre-commit 훅: 각자 설치해야 하고 CI 를 대신하지 못한다. CI 와 같은 입구(`scripts/check.py`)를 손으로 부르는 쪽을 택했다.
- `S603` 을 켜고 호출마다 noqa: 호출 10여 곳에 같은 주석이 붙을 뿐이다. 위험한 형태는 다른 규칙이 잡는다.
