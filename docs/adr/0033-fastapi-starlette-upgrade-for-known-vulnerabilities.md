# ADR-0033 FastAPI · Starlette 의 판을 올린다: 알려진 취약점, 그리고 Starlette 를 직접 고정한다

- 상태: 채택 (2026-11-15)
- 관련: 백엔드 감사 보고서(`docs/audit/2026-11-backend-audit.md` §12.3 결정 7 · 10), ADR-0030 (CI 품질 게이트 — audit 잡),
  ADR-0024 (참조 이미지 업로드), 과제 문서의 개발 환경

## 맥락
- 과제 문서의 개발 환경은 FastAPI 0.115.13 이고 `pyproject.toml` 이 그 판을 정확히 고정했다. 그 FastAPI 는 `starlette<0.47.0` 을 요구해
  Starlette 가 0.46.2 에 묶여 있었다.
- CI 의 audit 잡(pip-audit)이 첫 실행(PR #36)에서 그 Starlette 의 알려진 취약점을 알렸다. OSV 로 본 것은 일곱 가지다 (2026-11-15 조회).

  | 취약점 | 심각도 | 무엇 | 고친 판 |
  |---|---|---|---|
  | CVE-2026-48818 | 높음 | Windows 의 `StaticFiles` 가 UNC 경로를 따라가 서버 쪽 요청 위조 · NTLM 자격 증명 유출 | 1.1.0 |
  | CVE-2025-62727 | 높음 | `FileResponse` 의 Range 머리글 병합이 O(n²) — 서비스 거부 | 0.49.1 |
  | CVE-2026-54283 | 높음 | urlencoded 폼에서 `request.form()` 의 한도가 무시된다 | 1.3.1 |
  | CVE-2025-54121 | 중간 | multipart 의 큰 파일을 읽을 때 서비스 거부 | 0.47.2 |
  | CVE-2026-48710 | 중간 | Host 머리글을 검사하지 않아 `request.url.path` 가 오염된다 | 1.0.1 |
  | CVE-2026-48817 | 중간 | `HTTPEndpoint` 가 임의의 HTTP 메서드를 속성으로 부른다 | 1.1.0 |
  | CVE-2026-54282 | 낮음 | 요청 경로가 authority 에 이어 붙어 `request.url.hostname` 이 오염된다 | 1.3.0 |

- 이 앱은 그 가운데 세 기능을 쓴다: 정적 파일 마운트(`presentation/app.py` 의 `StaticFiles` — 서버가 화면을 함께 낼 때),
  영상 · 클립 · 참조 이미지 내려받기(`presentation/media.py` 의 `FileResponse`. Range 로 영상 탐색을 받는다),
  참조 이미지 업로드(`presentation/routers/reference_images.py` 의 `UploadFile`, multipart). 개발 · 시연 기기에는 Windows 가 있다.
- 사용자 결정 (2026-11-15): FastAPI 를 올린다. 잠금 파일은 지금 두지 않는다.

## 결정

### 1. `fastapi==0.133.0`, `starlette==1.3.1`
- 고르는 기준: **일곱 가지가 모두 고쳐진 Starlette(1.3.1 이상)를 받는 가장 낮은 FastAPI.** PyPI 의 메타데이터로 보면 FastAPI 는 0.132.x 까지
  `starlette<1.0.0` 을 요구하고, 0.133.0 (2026-02-24)이 그 상한을 없앴다. 릴리스 노트가 "Starlette 1.0.0+ 지원" 인 바로 그 판이다.
  가장 낮은 판을 고르는 까닭은 과제 문서의 환경에서 덜 벗어나고, 함께 들어오는 변화(마이너 판 18개)가 가장 적어서다.
- Starlette 는 고쳐진 판 가운데 가장 낮은 1.3.1 이다 (2026-06-12). FastAPI 0.133.0 이 나올 때의 Starlette 와 가장 가깝다.
- OSV 에 FastAPI 0.133.0 과 Starlette 1.3.1 의 알려진 취약점은 없다 (2026-11-15 조회).
- 함께 들어오는 패키지는 `annotated-doc` 하나다 (FastAPI 0.133.0 이 요구한다. 5KB).
  `uvicorn==0.34.2` · `python-multipart` · `pydantic` · `anyio` 는 그대로다.

### 2. Starlette 를 `pyproject.toml` 에 직접 고정한다
- FastAPI 는 0.133.0 부터 Starlette 의 판을 위로 묶지 않는다 (`starlette>=0.40.0`). 직접 고정하지 않으면 설치할 때마다 그때의 가장 새 Starlette 가
  들어온다 — CI · Docker 이미지 · 번들 · 개발 환경이 서로 다른 판으로 돌고, 커밋이 없어도 CI 의 결과가 바뀔 수 있다.
  전에는 FastAPI 의 상한이 Starlette 를 0.46.x 에 묶어 주었다. 그때만큼 정해져 있게 하려면 직접 고정해야 한다.
- 이 앱은 Starlette 를 직접 import 하기도 한다 (`presentation/errors.py` 의 `StarletteHTTPException`). 직접 쓰는 패키지를 직접 적는 것이다.
- 잠금 파일은 두지 않는다 (보고서 §12.3 결정 7). 다른 전이 의존성(pydantic · anyio …)은 전처럼 고정하지 않는다.

### 3. 테스트 클라이언트
- Starlette 1.x 의 `TestClient` 는 `httpx2` 를 쓴다. 없으면 `httpx` 로 물러나며 경고한다. dev extra 에 `httpx2>=2.0` 을 적었다
  (지금까지는 `llm` extra 의 `openai` 가 가져와 깔려 있었다). `httpx` 는 남긴다 — openai 의 오류 객체를 만드는 테스트가 쓴다.

### 4. 판을 올린 뒤에 환경과 문서가 뒤처지지 않게
- `tests/unit/test_test_harness.py::test_the_installed_packages_are_the_pinned_ones` — `pyproject.toml` 이 `==` 로 고정한 판과 설치된 판이 다르면
  실패하고, 다시 설치하라고 알린다. 여러 작업이 한 가상 환경을 같이 쓰므로 고정을 올린 뒤에 환경이 뒤처지기 쉽다.
  뒤처진 환경에서는 명세 스냅숏 테스트가 까닭을 알 수 없는 모양으로 실패한다.
- `tests/architecture/test_docs.py::test_the_documented_stack_names_the_pinned_versions` — 설계 문서의 스택 줄이 고정과 같다.

## 결과
- audit 잡이 알리던 것 가운데 Starlette 의 것이 없어진다 (CI 의 다음 실행에서 확인한다). 남는 것은 러너 환경의 `setuptools` 두 건이다 —
  프로젝트의 의존성이 아니라 검사한 환경에 깔려 있던 것이고 이 결정의 범위가 아니다. 그대로 둔다.
- OpenAPI 명세(`docs/openapi.json`)가 두 곳 바뀐다. 요청과 응답의 모양은 같다.
  - 참조 이미지 업로드의 파일 필드: `"format": "binary"` → `"contentMediaType": "application/octet-stream"` (OpenAPI 3.1 의 표기).
  - FastAPI 의 기본 422 스키마(`ValidationError`)에 `ctx` · `input` 이 생겼다. 이 앱의 오류 응답은 `{error_code, error_message}` 이고,
    그 기본 스키마는 경로 매개변수만 있는 아홉 오퍼레이션의 명세에만 남아 있는 것이다 (감사 D-05).
- 테스트는 그대로 통과한다 (명세 스냅숏만 다시 만들었다). 실제 서버(uvicorn)를 루프백에 띄워 보낸 요청 여섯 가지 —
  건강 검사, 없는 버전, 프로젝트 만들기, 깨진 본문, 참조 이미지 업로드, 명세 — 의 응답이 올리기 전과 같다.
- 과제 문서의 개발 환경(FastAPI 0.115.13)에서 벗어난다. `docs/ARCHITECTURE.md` 의 스택 줄에 그렇게 적었다.
- 다음에 올릴 때: FastAPI 와 Starlette 의 고정을 함께 보고, 같이 쓰는 가상 환경을 다시 설치하고,
  `python -m frame.tools.export_openapi` 로 명세를 다시 만든다. audit 잡이 Starlette 의 새 취약점을 알리면 Starlette 만 올릴 수 있다
  (FastAPI 가 위로 묶지 않는다).

## 대안
- **그대로 두고 쓰는 곳에서 막기** (정적 파일 마운트를 끄고, Range 머리글을 서버 앞에서 거르고 …): 일곱 가지를 하나씩 따로 막아야 하고,
  새로 알려지는 것마다 다시 해야 한다. 채택하지 않는다.
- **가장 새 FastAPI (0.162.7)**: `opentelemetry-api` 가 새로 들어오고, 0.133.0 뒤로 마이너 판 29개의 변화가 한 번에 들어온다.
  취약점을 고치는 데 필요하지 않다.
- **Starlette 를 범위로** (`starlette>=1.3.1,<2`): 하한은 지켜지지만 설치할 때마다 판이 달라진다. 잠금 파일이 없는 지금은 정확한 고정이 낫다.
- **잠금 파일로 전부 고정**: 도구(pip-tools · uv)가 필요하다. 지금은 두지 않기로 했다 (결정 7).
