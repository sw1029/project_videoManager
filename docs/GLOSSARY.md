# 용어집 — 낱말 하나에 뜻 하나

코드·문서·리뷰에서 쓰는 낱말이다. 한국어 이름과 코드 이름을 짝짓는다. 새 이름은 여기 있는 낱말로 짓고, 없으면 여기에 먼저 더한다
(ADR-0031 규칙 2). 어디에 있는지는 [MODULE_MAP.md](MODULE_MAP.md) 가, 왜 그렇게 정했는지는 `docs/adr/` 가 말한다.

## 1. 기본 낱말

| 낱말 | 코드 이름 | 뜻 |
|---|---|---|
| 프로젝트 | `Project` | 요구사항 하나에서 시작하는 작업 단위. 버전들을 가진다 |
| 버전 | `VideoVersion` | 한 번의 구조화·생성 결과. 상태(`VersionState`)를 가진다 |
| 원본 버전 · 수정 버전 | `parent_version_id` 가 없다 · 있다 | 수정 버전은 부모 버전의 IR 을 고쳐 만든 버전이다 |
| 중간표현 (IR) | `IntermediateRepresentation` | 요구사항을 장면 · 객체 · 요구조건으로 구조화한 것 |
| 장면 | `SceneSpec` | 시간 구간 하나. 객체와 요구조건이 속한다 |
| 객체 | `EntitySpec` (`EntityKind`) | 장면에 나오는 것 — 인물 · 동물 · 물건 · 배경 · 글자 |
| 요구조건 | `RequirementSpec` | "객체.술어 = 값" 또는 객체 사이의 관계. 검수의 단위다 |
| 술어 어휘 | `PredicateVocabulary` | 요구조건에 쓸 수 있는 술어의 목록 |
| 구조화 | `RequirementStructurer` | 요구사항(글 · DSL)을 IR 로 바꾸는 일 |
| 보정 | `adapters/structurer/repair.py` | 구조화 결과에서 고칠 수 있는 어긋남을 고치는 일. **다시 맞추기(REPAIR)와 다른 뜻이다** |
| 생성 | `VideoGenerator` | 장면 하나의 클립을 만드는 일 |
| 장면 클립 | `GeneratedClip` | 장면 하나의 영상 |
| 클립 정규화 | `VideoNormalizer` | 모델이 돌려준 클립을 한 규격(길이 · fps · 크기 · 형식)으로 맞추는 일 |
| 최종 영상 | `VideoArtifact` | 장면 클립을 이은 결과 |
| 추적 | `ObjectTracker` | 영상에서 객체의 위치를 따라가는 일 |
| 검수 | `Checker`, `CheckSuite` | 만든 영상이 요구를 지켰는지 판정하는 일 |
| 산출물 저장소 | `ArtifactStore` | 클립 · 이미지 · 기록을 주소(URI)로 저장하는 곳 |
| 포트 · 어댑터 | `application/ports/` · `adapters/` | 바깥에 요구하는 계약 · 그 구현 (ADR-0001) |

## 2. 같은 낱말이 여럿을 가리키던 곳

### '수정' 계열
| 낱말 | 코드 이름 | 뜻 |
|---|---|---|
| 수정 버전 | `revision`, `CreateRevision` | 부모 버전을 고쳐 만드는 새 버전 |
| 수정 의도 | `RevisionIntent` | 사용자가 수정으로 바라는 것 — 무엇을 어떻게, 무엇은 그대로 |
| 값 바꾸기 | `RevisionKind.CHANGE`, `AttributeChange` | 요구조건의 값을 바꾼다 (컵 색을 빨강에서 청록으로) |
| 다시 맞추기 | `RevisionKind.REPAIR` | 요구조건은 그대로 두고, 반영되지 않은 장면을 다시 만든다 (ADR-0017) |
| 유지할 요소 | `KeepItem` (`KeepAspect`) | 수정에서 그대로 두라고 사용자가 고른 것 — 객체의 모습 · 동작 · 형태, 장면의 배경 · 조명 · 구도 |
| 수정 지시 | `RevisionDirective` | 생성기에 넘기는 "이 장면을 왜 다시 만드는가" (종류 · 고칠 요구조건 · 이전 값 · 유지할 요소) |
| 수정 전파 | `domain/revision/propagation.py` (대상 장면은 `changes.py`) | 값 바꾸기가 어느 장면에 미치는지 (`ChangeScope`) |
| 수정 요청 풀기 | `domain/revision/resolution.py` `resolve_revision` | 수정 요청을 CHANGE · REPAIR 로 풀어 수정 버전의 초안을 만드는 규칙. **변경 계획이 아니다** (옛 이름 `revision_planning` 이 헷갈려 옮기면서 바꿨다) |
| 변경 계획 | `PlanRevision`, `RevisionPlan` | 수정을 실행하기 전에 보여 주는 미리보기 — 어느 장면을 다시 만들고, 어떻게 · 얼마에 (ADR-0019, 0020) |
| 변경 요청 해석 | `ChangeRequestInterpreter` | 글로 쓴 변경 요청을 수정 의도로 읽는 일 (ADR-0019) |
| 부모 클립 편집 | `ProductionMethod.EDIT`, `ParentClip` | 다시 만드는 장면을 새로 생성하지 않고 부모 버전의 클립을 고쳐 만드는 방식 (ADR-0016) |
| 수정 실험 평가 | `evaluation/edit_metrics.py` | 수정 실험의 성공 지표 (지표 1). 여기의 `edit` 은 '수정' 이다 |

`edit` 이라는 영어 낱말은 부모 클립 편집에만 새로 쓴다. 수정 버전은 `revision`, 값 바꾸기는 `change` 다.

### '참조' 계열
| 낱말 | 코드 이름 | 뜻 |
|---|---|---|
| 사용자 참조 이미지 | `ReferenceImage` · `ReferenceOrigin.USER` | 사용자가 올린 이미지 |
| 정체성 기준 이미지 | `ReferenceImage` · `ReferenceOrigin.DERIVED`, `identity_references` | 만든 클립에서 뽑은 객체의 모습. 다른 장면에서 같은 객체가 같게 나오게 한다 (ADR-0015) |
| 입력 참조 | 제공자 요청의 `input_references` | 영상 모델에 보내는 참조 (위의 두 가지와 편집 때의 부모 클립이 여기에 실린다) |
| 영상 참조 | `video_reference` (편집 방식의 하나) | 부모 클립을 영상으로 참조시켜 편집하는 방식 |

### '검수' 계열
| 낱말 | 코드 이름 | 뜻 |
|---|---|---|
| 검수기 | `Checker` | 검수 한 종류의 구현 |
| 검수 묶음 | `CheckSuite` (`application/checks.py`) | 켜진 검수기를 차례로 돌린다. 검수기가 예외로 끝나면 그 자리에 미판정을 남긴다 |
| 검수 결과 (돌려주는 값) | `CheckOutcome` | 검수기가 돌려주는 값 |
| 검수 결과 (저장되는 값) | `CheckResult` (`domain/checks.py`) | 버전에 저장되는 결과. 구간 경고(`WarningInterval`)를 가질 수 있다 |
| 판정 | `Verdict` — `PASS` · `FAIL` · `UNDETERMINED` | 통과 · 기준 미달 · 미판정. 미판정은 통과로 세지 않는다. `FAIL` 은 실행 오류(`FAILED`)와 다르다 |
| 요구 반영 검수 | `CheckType.REQUIREMENT` | 요구조건이 영상에 반영됐는가 (ADR-0018) |
| 일관성 검수 | `CheckType.CONSISTENCY` | 객체가 장면 사이에서 같은 모습인가 (ADR-0015) |

### '보존' 계열 — 검수 종류 `PRESERVATION` 하나에 둘이 든다
| 낱말 | 코드 이름 | 뜻 |
|---|---|---|
| 보존 검수 | `SsimPreservationChecker` | 다시 만든 장면이 부모 클립과 화면 전체로 얼마나 같은가 (구조 유사도, ADR-0016) |
| 유지 요소 검수 | `VlmPreservationChecker` | 사용자가 고른 '유지할 요소' 가 요소 단위로 그대로인가 (ADR-0018) |

문서에서 '유지 요소' 와 '유지할 요소' 는 같은 것이다. 사용자가 고르는 것은 '유지할 요소'(`KeepItem`), 그 검수는 '유지 요소 검수' 로 쓴다.

### '조립' 과 '계획'
| 낱말 | 코드 이름 | 뜻 |
|---|---|---|
| (최종 영상) 조립 | `VideoAssembler` | 장면 클립을 이어 최종 영상을 만드는 일 |
| 조립 지점 | `config/` (`Settings` → `AdapterRegistry` → `Container`) | 설정에 따라 어댑터를 골라 엮는 곳. 등록은 `config/registrations/<포트>.py` |
| 변경 계획 | `PlanRevision` | 위 '수정' 계열 참고. 수정 요청 풀기(`domain/revision/resolution.py`)는 변경 계획이 아니다 |

## 3. 한 번만 만들고 한 번만 과금하기

| 낱말 | 코드 이름 | 뜻 |
|---|---|---|
| 선택적 재생성 | `scene_fingerprint` 를 부모와 견준다 | 수정 버전에서 조건이 같은 장면은 다시 만들지 않고 부모의 클립을 쓴다 (ADR-0003) |
| 생성 캐시 | `ClipCache` (`CachingVideoGenerator`) | 같은 결과 키의 클립이 있으면 다시 생성하지 않는다 (ADR-0013) |
| 작업 원장 | `JobLedger`, `JobRecord` | 제공자에 제출한 유료 작업의 기록 (`submitted` · `completed` · `failed` · `released`). 프로세스가 죽어도 같은 작업을 이어 받는다 |
| 프로필 | `VideoGenerator.profile()` | 결과를 바꾸는 생성기 설정 (모델 · 해상도 · 화면비 · fps) |
| 임시 결과 | `provisional` | 요청한 방식으로 만들지 못해 다른 방식으로 낸 결과. 그 요청의 결과 키로 남기지 않는다 (ADR-0021) |
| 재시작 복구 | `RecoverPendingVersions` | 지난 프로세스가 남긴 버전을 다시 넣거나 오류로 끝낸다 (ADR-0013) |
| 리스 | `FRAME_WORKER_LEASE_S` | 처리 중인 버전의 작업자가 살아 있다고 보는 시간. 넘기면 회수한다 |

### 해시와 키 — 무엇을 담고 누가 판단에 쓰는가
일곱 가지가 비슷한 이름이다. 위에서 아래로 갈수록 담는 것이 는다.

| 이름 | 어디 | 담는 것 | 누가 판단에 쓰는가 |
|---|---|---|---|
| IR 해시 `IR.fingerprint()` → `frozen_fingerprint` | `domain/ir.py` | IR 전체 | 생성 접수 때 '조건 고정' 값으로 저장한다 |
| 장면 해시 `IR.scene_fingerprint(scene_key)` | `domain/ir.py` | 장면 조건(구간 · 설명 · 객체 · 요구조건) + 그 장면 객체의 정의 | **버전 사이 재사용** — 부모와 같으면 다시 만들지 않는다 |
| 객체 모습 해시 `entity_appearance_fingerprint()` | `domain/identity.py` | 객체 정의 + 그 장면의 고정 속성 | **정체성 기준 이미지의 유효성** — 모습이 달라진 객체의 기준은 쓰지 않는다 |
| 조건 해시 `SceneGenerationRequest.fingerprint` | `application/ports/generator.py` | 장면 조건 + 객체 정의 + 사용자 참조 이미지 + seed. `version_id` 는 뺀다 | 클립의 `fingerprint` 로 남는다 (어느 조건으로 만든 클립인가) |
| 생성 해시 `generation_fingerprint` | 같은 모듈 | 조건 해시 + 정체성 기준 이미지 + 유지할 요소 + (REPAIR 일 때) 고치려는 요구조건 | 결과 키의 기본값 |
| 결과 키 `VideoGenerator.result_key(request)` | 같은 모듈, 편집하는 생성기가 재정의 | 생성 해시 + (부모 클립을 편집해 만들 때) 부모 클립 | **생성 캐시 · 작업 원장** 의 요청 쪽 키 |
| 캐시·원장 키 `generation_key(result_key, profile)` | `adapters/generator/cache.py` | 결과 키 + 프로필 | 생성 캐시와 작업 원장이 실제로 찾는 키 |

- 더할 것이 없으면 아래 값은 위 값과 같다 (기준 이미지도 수정 지시도 없는 요청의 생성 해시는 조건 해시다). 그래서 예전 키와 호환된다.
- 결과를 바꾸는 값을 새로 들일 때는 이 표에서 어느 줄에 들어가야 하는지 먼저 정한다. 빠뜨리면 다른 요청이 같은 키가 되어 앞선 결과를 받는다
  (감사 A-33: REPAIR 가 고치려는 요구조건이 키에 없었다).
- `generation_key` 의 첫 인자 이름은 아직 `fingerprint` 다. 넘기는 값은 결과 키다 (이름은 머지 뒤에 맞춘다, 감사 P-08).

## 4. 상태

| 낱말 | 코드 이름 | 뜻 |
|---|---|---|
| 버전 상태 | `VersionState` — `STRUCTURING` → `DRAFT` → `QUEUED` → `PROCESSING` → `COMPLETED` / `FAILED` | 구조화 중 → 초안 → 대기 → 처리 중 → 끝남 / 실행 오류. 검수 판정이 `FAIL` 이어도 버전은 `COMPLETED` 다 |
| 조건 고정 | `frozen_fingerprint` | 생성을 접수하면 IR 을 더 고칠 수 없다 |
| 뼈대 어댑터 | `skeleton(...)` (`config/registrations/_dependencies.py`) | 자리만 있고 구현이 없는 어댑터. 고르면 기동에서 거절한다 |
| 호환 모듈 | 옛 경로의 모듈 | 옮긴 모듈의 이름만 다시 내보낸다 (ADR-0031 3절). 그 경로를 쓰는 작업 중 브랜치가 있을 때만 둔다 — 2026-11-15 에 모두 걷어내 지금은 없다 |
