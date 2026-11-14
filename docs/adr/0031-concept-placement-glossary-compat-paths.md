# ADR-0031 개념 단위 배치 규칙 · 용어집 · 옛 경로의 호환 모듈

- 상태: 채택 (2026-11-14)
- 관련: 백엔드 감사 보고서(`docs/audit/2026-11-backend-audit.md` §5.1), ADR-0001 (4계층 + Ports & Adapters),
  ADR-0030 (CI 품질 게이트와 기준선 방식), 모듈 지도 `docs/MODULE_MAP.md`, 용어집 `docs/GLOSSARY.md`

## 맥락
- 기준 (사용자, 2026-11-14): 리팩터링은 **개념 단위 추상화**를 기준으로 모듈을 나눠, 뒤에 들어오는 사람의 인지 부담을 줄인다.
  디렉터리가 깊어지는 것은 괜찮다.
- 계층 방향(ADR-0001)은 지켜지고 있었다. 감사에서 걸린 것은 계층 **안**의 배치다.
  - **섞임**: 한 모듈·패키지에 여러 개념이 있다. `adapters/generator/` 에 제공자 구현 · 복원력 데코레이터 · 한 번만 과금하기 위한 기록 ·
    다른 포트인 조립기가 함께 있었고, `config/registry.py` 의 등록 함수 하나가 포트 10종을 다뤘다.
  - **흩어짐**: 한 개념이 같은 계층의 여러 곳에 있거나 같은 코드가 여러 벌이다. ffmpeg 실행기가 두 벌(작업 중인 브랜치까지 세 벌),
    키 찾기·가리기가 다섯 군데였다.
  - **이름**: 방법이나 기술 종류로 지은 이름(`common.py`, `video_tools.py`, `decorators.py`), 같은 낱말이 여러 개념을 가리키는 경우
    ('수정' 계열 넷, '참조' 넷, '보존' 둘).
  - **길잡이가 없음**: 패키지 24개 가운데 설명이 없는 것 10개, 밖에서 쓰는 이름을 적지 않은 것 8개. "어디에 무엇이 있나" 를 알려 주는 문서가 낡았다.
- 여러 브랜치가 동시에 진행 중이다. 모듈을 옮기면 그 브랜치들의 import 가 깨지고, 이름만 바꾸는 커밋은 머지에서 충돌한다.

## 결정

### 1. 배치 규칙
1. **패키지 하나는 개념 하나다.** 파일 크기나 기술 종류(스키마·열거형·데코레이터·도구)로 묶지 않는다. 디렉터리가 깊어져도 된다.
2. **이름은 개념의 낱말로 짓는다.** 용어집(`docs/GLOSSARY.md`)의 낱말을 쓰고, `utils`·`common`·`helpers`·`tools` 처럼 방법을 말하는 이름은 쓰지 않는다.
   새 이름은 용어집에 있는 낱말로 짓고, 없으면 용어집에 먼저 더한다. 기존 이름은 옮기는 김에만 맞춘다 (이름만 바꾸는 커밋은 만들지 않는다).
3. **패키지의 입구가 보이게 한다.** `__init__.py` 머리에 세 가지를 적는다 — 맡는 개념, 밖에서 쓰는 이름, 안의 모듈 지도.
   `__all__` 로 밖에서 쓸 이름만 내보낸다. 새로 만들거나 옮기는 패키지의 안쪽 구현 모듈은 `_` 로 시작하고, 패키지 밖의 코드는 `_` 모듈을
   import 하지 않는다 (테스트는 예외).
4. **모듈 지도를 코드와 함께 고친다.** `docs/MODULE_MAP.md` 가 개념 → 패키지 → 포트·주요 클래스를 잇는다.
   계층을 건너는 길(도메인 → 포트·유스케이스 → 어댑터)은 흩어짐이 아니다. 그 길을 잇는 것이 이 문서다.
5. **어댑터 패키지끼리는 서로 import 하지 않는다.** 함께 쓰는 것은 `adapters/shared/` 에 둔다 (ADR-0030 의 어댑터 경계).
6. **옮긴 모듈은 옛 경로에서 다시 내보낸다** (아래 3절. 2026-11-15 보완: 그 경로를 쓰는 작업 중 브랜치가 있을 때만).

### 2. 어디에 무엇을 두는가
목표 배치는 감사 보고서 §5.1 '목표 배치' 에 있다. 자주 묻는 자리만 적는다.

| 넣을 것 | 자리 |
|---|---|
| 새 포트의 구현 | `adapters/<개념>/` 패키지. 등록은 `config/registrations/<포트>.py` 한 줄 |
| 어댑터 둘 이상이 쓰는 도구 | `adapters/shared/` — 영상 처리 `media/`, 모델의 답 읽기 `vlm/`, 키 `api_keys.py`, 전송 `http.py`, ID·시계 `runtime.py` |
| 새 업무 개념 (도메인) | `domain/<개념>/` 하위 패키지. 열거형은 그 개념의 모듈 옆에 둔다 |
| 유스케이스 | `application/use_cases/<유스케이스>.py` 하나. 입출력 타입은 그 모듈 옆에 |
| 한 제공자의 구현이 커질 때 | 그 제공자의 하위 패키지. 모듈 이름은 그 제공자가 하는 일의 낱말 (제출 · 대기 · 원장으로 이어 받기 …) |
| 명령줄 도구 | `tools/<도구>.py`, 커지면 `tools/<도구>/` |

### 3. 옛 경로의 호환 모듈
- 모듈을 옮기면 옛 경로에 **이름만 다시 내보내는 모듈**을 남긴다. 구현을 담지 않고, 새 자리에서 가져온 이름을 `__all__` 로 내보내기만 한다.
  옛 경로의 이름은 새 자리의 것과 같은 객체다 (`isinstance`·`except` 가 그대로 맞는다).
- 이 브랜치의 코드는 새 경로를 쓴다. 다른 브랜치가 고치고 있는 파일만 옛 경로를 그대로 둔다 (기준선에 적는다).
- **걷어내는 때**: 작업 중인 브랜치가 모두 dev 에 들어가고, 그 코드의 import 를 새 경로로 고친 뒤. 호환 모듈과 기준선을 함께 지운다.
- 호환 모듈로 남겼던 옛 경로 (2026-11-14 에 남기고 2026-11-15 에 걷어냈다 — 아래 보완)

  | 옛 경로 | 새 자리 |
  |---|---|
  | `frame.adapters.common` | `frame.adapters.shared.runtime` |
  | `frame.adapters.http_transport` | `frame.adapters.shared.http` |
  | `frame.adapters.video_tools` | `frame.adapters.shared.media` (`frames` · `probe` · `similarity` · `ffmpeg`) |
  | `frame.adapters.vlm_answers` | `frame.adapters.shared.vlm.answers` |
  | `frame.adapters.generator.ffmpeg` | 클립 정규화 `frame.adapters.generator.normalizer`, ffmpeg 찾기 `frame.adapters.shared.media.ffmpeg`, 클립 검사 `frame.adapters.shared.media.probe` |
  | `frame.application.ports.generator.VideoAssembler` (이름 하나) | `frame.application.ports.assembler.VideoAssembler` |
  | `frame.domain.services.propagation` | `frame.domain.revision` (`changes` · `propagation`) |
  | `frame.domain.services.revision_planning` | `frame.domain.revision.resolution` |

  최종 영상 조립기(`FfmpegVideoAssembler` · `FakeVideoAssembler`)는 `frame.adapters.assembler` 로 옮겼고 옛 경로를 남기지 않았다
  (어댑터 패키지끼리 import 하지 않는다. 작업 중인 브랜치의 새 코드는 이 이름을 쓰지 않는다).
- **보완 (2026-11-15, 감사 P-17) — 호환 모듈을 걷어냈다.** 작업 중이던 브랜치(#21 · #10·#11·#12 · #17 · theme-series)가 모두
  dev 에 들어갔고, 원격과 로컬의 다른 브랜치에 옛 경로를 쓰는 코드가 없음을 확인했다. 남은 import 를 새 경로로 고치고 위 표의
  호환 모듈과 기준선(`COMPAT_*`), `tests/unit/test_compat_paths.py` 를 지웠다. 표는 옛 브랜치를 가져올 때 고칠 자리를 찾는
  기록으로 남긴다. 같은 표가 `tests/architecture/test_moved_paths.py` 에 있어, 그 경로가 다시 생기거나 쓰이면 테스트가 실패한다.
- **보완 (2026-11-15) — 호환 모듈은 그 경로를 쓰는 작업 중 브랜치가 있을 때만 남긴다.** 옮기기 전에 원격 · 로컬 브랜치에서
  그 경로를 쓰는 곳을 찾는다. 없으면 옛 경로를 남기지 않고, `test_moved_paths.py` 의 표에 옛 경로와 새 자리를 적는다.
- 호환 모듈 없이 옮긴 것 (2026-11-15)

  | 옛 경로 | 새 자리 |
  |---|---|
  | `frame.domain.services.identity` | `frame.domain.identity` |
  | `frame.domain.services.comparison` | `frame.domain.comparison` |
  | `frame.evaluation.edit_cases` · `edit_runner` · `edit_report` · `edit_metrics`, `frame.evaluation` 이 내보내던 이름 | `frame.evaluation.edits` (패키지 입구에서 가져온다) |

### 4. 용어집
- `docs/GLOSSARY.md` — 낱말 하나에 뜻 하나, 한국어 이름과 코드 이름을 짝짓는다. 낱말은 연구 문서와 ADR 의 것을 따른다.
- 같은 낱말이 여러 개념을 가리키던 곳은 용어집이 가른다 ('수정' · '참조' · '검수' · '보존' · '조립').
- 해시·키 일곱 가지(무엇을 담고 누가 판단에 쓰는가)는 용어집의 표 하나에 모은다.

### 5. 지키는 검사 (`tests/architecture/`, ADR-0030 의 기준선 방식)
| 규칙 | 검사 |
|---|---|
| 패키지에 설명과 `__all__` 이 있다 (규칙 3) | `test_package_entrances.py` — 없는 패키지는 기준선, 줄이기만 한다 |
| 모듈이 500줄을 넘지 않는다 (규칙 1 의 신호) | `test_module_size.py` — 넘는 모듈은 기준선 |
| 어댑터 패키지끼리 import 하지 않는다 (규칙 5) | `test_adapter_boundaries.py` |
| 어댑터 최상위에는 패키지만 있다 (규칙 5) | `test_adapter_boundaries.py` |
| 걷어낸 옛 경로가 다시 생기지 않고, 그 경로로 import 하는 곳이 없다 (규칙 6. 2026-11-15 에 호환 모듈을 걷어내며 바꿨다) | `test_moved_paths.py` |
| 모듈 지도의 패키지 목록이 실제와 같다 (규칙 4) | `test_docs.py` |

규칙 2(이름)는 검사로 지키지 못한다. 리뷰에서 용어집과 견준다.

## 결과
- 새로 온 사람이 패키지 이름과 `__init__.py` 의 설명만 보고 자리를 찾는다. 패키지 하나를 읽을 때 머리에 담을 개념이 하나다.
- 호환 모듈이 있는 동안에는 같은 이름에 경로가 둘이다. 검사가 새 코드의 옛 경로 사용을 막고, 걷어내는 때를 위 3절에 적었다.
- 모듈이 늘고 경로가 길어진다. 모듈 지도와 패키지 설명이 그 값을 치른다 — 둘을 고치지 않으면 테스트가 실패한다.
- 옮기는 일은 동작을 바꾸지 않는다. 동작을 바꾸는 수정은 따로, 테스트를 먼저 쓰고 한다.

## 대안
- **기술 종류로 묶기** (`schemas/`·`enums.py`·`decorators.py`): 찾는 사람은 "수정 의도가 어디 있나" 를 묻지 "열거형이 어디 있나" 를 묻지 않는다. 채택하지 않는다.
- **옛 경로를 바로 지우기**: 작업 중인 브랜치 넷의 import 가 깨지고 머지마다 고쳐야 한다. 호환 모듈로 미룬다.
- **`__init__.py` 에서 모두 다시 내보내기** (`adapters.shared` 가 모든 이름을 내보낸다): import 한 줄이 짧아지지만, 무엇이 어느 개념의 것인지
  import 문에서 보이지 않고, 패키지를 import 하면 쓰지 않는 모듈까지 읽는다 (시연 런처는 의존성 설치 전에 `shared.api_keys` 만 읽어야 한다).
  `shared` 와 그 하위 패키지는 모듈 경로로 import 한다.
- **import-linter 로 지키기**: 새 개발 도구가 필요하다. pytest 로 쓴 구조 규칙으로 충분하다 (ADR-0030).
