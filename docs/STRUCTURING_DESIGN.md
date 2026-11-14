# 요구사항 구조화(US-06) 구현안 상세 검토

> 대상: `RequirementStructurer` 포트 뒤에 들어갈 실제 구조화 파이프라인.
> 근거: Product Backlog US-06 (AC-11~14), Sprint 1 T2·T4·T6, 문제정의서 리스크 2("구조화 결과의 사용자 확인·수정"),
> 아이디에이션 3(태그 블록 입력)·10(참조 이미지 → 속성 자동 분해), 페이퍼 프로토타입 ① 화면, ADR-0002/0007.

---

## 0. 결론 요약

자연어 브리프를 IR 로 바꾸는 일은 **"LLM 한 번 호출"이 아니라 6단계 파이프라인**으로 만든다.
모델은 초안을 만들 뿐이고, 결정적인 **보정(Repair)·검증·보고서** 단계가 스키마 규칙을 책임진다.
사용자는 보고서를 보고 DRAFT 의 IR 을 고친 뒤 생성을 접수한다. 화면이 블록(태그) 입력을 쓰면 LLM 없이 IR 을 직접 넣을 수도 있다.

핵심 결정 5개

| # | 결정 | 이유 |
|---|---|---|
| 1 | 입력 모드 3가지를 모두 지원: 자연어 브리프 · 화면 블록(IR 직접) · DSL(개발/테스트) | 프로토타입 ① 화면은 블록 입력이고, US-06 은 자연어 분해를 요구한다. 둘 다 같은 IR 로 수렴 |
| 2 | LLM 호출은 `LLMClient` 포트 뒤에 두고 구조화 출력(JSON Schema)을 요구한다 | 모델(OpenAI/Anthropic/로컬 vLLM) 교체 가능, 파싱 실패 최소화 |
| 3 | 모델 출력은 반드시 결정적 **Repair** 를 거친다 (술어 정규화, 타임라인 스냅, 객체 연결, 중복 제거) | 스키마 규칙을 모델에 의존하지 않는다. 실패를 "보고"로 바꿔 사용자가 고치게 한다 |
| 4 | 구조화 보고서(`StructuringReport`)를 버전에 저장하고 API 로 노출, DRAFT IR 수정 API 추가 | 리스크 2 대응. "무엇을 추출·보정·누락했는가"가 보여야 검수(US-04)와 연결된다 |
| 5 | 골드셋 기반 평가 하네스(객체 재현율, 요구조건 F1, 장면 경계)로 모델·프롬프트 변경을 회귀 검증 | T2 "필수항목 누락 오류 확인", AC-11 을 수치로 판정 |

---

## 1. 요구와 제약 (문서 근거)

| 출처 | 요구 | 구현안 반영 |
|---|---|---|
| US-06 AC-11 | 자연어 기획 프롬프트 → 객체·속성·시간/장면 단위 자동 분해 | 4장 파이프라인 (추출 → 보정 → 검증) |
| US-06 AC-12 | 요구조건별 반영 여부·매칭률·경고 | 요구조건 키가 검수 결과의 기준 → 구조화 단계에서 키가 안정적으로 만들어져야 함 (ADR-0007) |
| US-06 AC-13 | 속성 블록 변경이 프롬프트 재작성 없이 반영 | IR 이 유일한 진실. 블록 편집 = DRAFT IR 수정 API, 수정 = `POST /revisions` |
| US-06 AC-14 | 요구사항 반영률 ≥ 80% | 분모는 구조화된 요구조건 수. 과추출(hallucination)은 분모를 부풀려 반영률을 낮추므로 보정 단계에서 근거 없는 조건을 걸러야 함 |
| T2 Done Criteria | 객체·속성·시간/장면 추출 + 필수항목 누락 오류 확인 | 보고서의 `errors`/`warnings` 와 골드셋 지표 |
| 리스크 2 | 구조화·매핑 오류 → 잘못된 장면 수정. 대응: 사용자 확인·수정 기능, 매핑 검증 절차 | 7장 사용자 확인 루프 |
| 아이디어 3 / 프로토타입 ① | [인물]·[배경]·[소품]·[행동] 블록 입력, 블록별 색상·위치 속성 | IR 직접 입력 모드 + 어휘 API (`GET /api/vocabulary/predicates`) |
| 아이디어 10 | 참조 이미지 → 구조화 속성 자동 완성 | 8장 참조 이미지 그라운딩 (VLM, Sprint 2) |
| 개발 환경 | Python 3.11, 로컬 GPU(RTX 3090) 1대, 비용 민감 | 추출용 모델은 API 또는 소형 로컬 모델. 생성 GPU 와 경쟁하지 않도록 비동기 작업 |

---

## 2. 현재 코드 상태와 간극

| 구성 요소 | 현재 | 간극 |
|---|---|---|
| 포트 `RequirementStructurer` | 입력(브리프·참조 이미지·목표 길이) → IR | 보고서(경고·보정 내역·신뢰도)를 돌려줄 자리가 없음 → `StructuringResult` 로 확장 |
| `RuleBasedStructurer` | 줄 단위 DSL, 결정적, 오류 메시지 | 자연어 불가. 개발·테스트·DSL 모드 전용으로 유지 |
| `LLMStructurer` | 시스템 프롬프트(어휘·규칙 포함), JSON → IR, 검증 | 모델 호출 없음, 구조화 출력 스키마 없음, 보정·재시도 없음, few-shot 없음 |
| 어휘 `vocabulary.py` | 술어 정규화·값 종류·관계 여부 | 프롬프트/JSON Schema 에 enum 으로 노출하는 코드 없음 |
| `IR.validate()` | 키·관계·타임라인 규칙 | 규칙 위반을 "고칠 수 있는 것"과 "못 고치는 것"으로 나누는 보정 단계 없음 |
| `StructureRequirements` | 비동기, FAILED 기록, 목표 길이 오차 | 보고서 저장 없음 (IR 직접 입력은 `CreateProject` 가 동기로 처리, ADR-0022) |
| API | `POST /projects {prompt}` · `{ir}`(201, 바로 DRAFT) · `PUT /versions/{id}/ir`(보정 메모 응답) — ADR-0022 | 보고서 조회 없음 (#16 보류) |
| 평가 | 지표 1 하네스(수정 실험)만 | 구조화 품질 골드셋·지표 없음 |

---

## 3. 입력 모드

```
자연어 브리프 ──▶ LLM 추출 ──┐
화면 블록 입력 ──▶ IR 문서  ──┼──▶ Repair ──▶ validate ──▶ DRAFT + 보고서
DSL(개발/테스트) ─▶ RuleBased ─┘
```

- **자연어 브리프** (`POST /api/projects {prompt}`): 본 문서의 주 대상. 비동기 작업.
- **IR 직접 입력** (`POST /api/projects {ir: {...}}`): 프로토타입 ① 화면이 블록을 IR JSON 으로 조립해 보낸다.
  LLM 을 거치지 않고 Repair → validate 만 수행하므로 동기 처리(바로 DRAFT)가 가능하다.
  `prompt` 와 `ir` 를 함께 보내면 `ir` 를 우선하고 `prompt` 는 `source_prompt` 로만 보관한다.
- **DSL**: `FRAME_STRUCTURER=rule_based`. 계약 테스트의 기준 구현이자 모델 없는 데모용.

세 모드 모두 같은 Repair·validate·보고서를 거치므로, 화면과 검수 로직은 입력 모드를 구분할 필요가 없다.

---

## 4. 권장 파이프라인 (`LLMStructurer` 내부)

```
StructuringRequest(prompt, reference_images, target_duration_ms)
   │
   ├─ 1 Prepare    브리프 정리: 공백/번호 정규화, 길이 힌트("15초") 추출, 장면 힌트("장면 1:") 감지, 언어 감지
   ├─ 2 Extract    LLMClient.complete_json(system, user, json_schema)  → raw IR 문서(dict)
   │                 · temperature 0, seed 고정(지원 시), few-shot 2~3개, 어휘·규칙을 시스템 프롬프트에 포함
   │                 · 파싱 실패/스키마 위반 시 오류를 다시 넣어 최대 2회 자기수정 재시도
   ├─ 3 Ground     (Sprint 2) 참조 이미지가 있으면 VLM 으로 객체 속성 추출 → 조건 병합, 충돌은 보고
   ├─ 4 Repair     결정적 보정 규칙 (6장). 자동 수정 / 경고 / 오류로 분류
   ├─ 5 Validate   IR.validate() + 목표 길이 오차 → 실패면 STRUCTURING_ERROR | VALIDATION_ERROR
   └─ 6 Report     StructuringResult(ir, report{warnings, repairs, errors, confidence, provenance})
```

단계별 책임

| 단계 | 입력 → 출력 | 실패 처리 |
|---|---|---|
| Prepare | 원문 → 정리된 브리프 + 힌트(목표 길이, 장면 수 상한) | 실패 없음 (힌트는 선택) |
| Extract | 브리프 → raw dict | 시간초과·API 오류 → `STRUCTURING_ERROR`(코드 `LLM_UNAVAILABLE`/`LLM_TIMEOUT`), 파싱 실패 2회 → `STRUCTURING_ERROR` |
| Ground | raw dict + 이미지 → raw dict (조건 추가, provenance=reference_image) | VLM 실패는 경고로 강등, 구조화는 계속 |
| Repair | raw dict → IR + repairs/warnings | 고칠 수 없는 항목(객체 없는 장면 등)은 `errors` 로 기록 후 중단 |
| Validate | IR → IR | `ValidationError` → FAILED, 메시지에 규칙 명시 |
| Report | 모든 단계의 기록 → 보고서 | — |

장면 수 상한: 숏폼 기준 장면 길이 3~8 s. `target_duration_ms / 3000` 을 상한으로 프롬프트에 알려 과분할을 막는다.

---

## 5. 모델 연동 설계

### 5.1 `LLMClient` 포트 (새 포트, `application/ports/llm.py`)

```python
class LLMClient(ABC):
    @abstractmethod
    def complete_json(self, system: str, user: str, json_schema: dict, *, timeout_s: float) -> str:
        """json_schema 에 맞는 JSON 문자열을 돌려준다. 실패는 LLMError(code) 로 보고한다."""
```

구현 후보 (전부 `adapters/structurer/llm/`):

| 키 | 구현 | 비고 |
|---|---|---|
| `fake` | `ReplayLLMClient`: 프롬프트 해시 → 고정 JSON(테스트 fixture) | 계약·유스케이스 테스트의 기본값 |
| `openai` | 구조화 출력(json_schema, strict) | 가장 적은 파싱 실패 |
| `anthropic` | tool use 의 `input_schema` 로 JSON 강제 | 긴 한국어 브리프에 유리 |
| `vllm` | 로컬 모델 + guided decoding(json schema) | 비용 0, 3090 에서 7~8B 모델 가능, 생성 GPU 와 시간 분리 필요 |

설정: `FRAME_LLM_PROVIDER`, `FRAME_LLM_MODEL`, `FRAME_LLM_TIMEOUT_S`, API 키는 환경 변수(저장소에 넣지 않음).

### 5.2 JSON Schema (`adapters/structurer/llm/schema.py`)

`IR.to_dict()` 형식과 1:1 인 JSON Schema 를 코드로 생성한다(수기 중복 금지).

- `schema_version` 상수, `entities[].kind` enum, `scenes[].requirements[]`.
- `predicate`: v1 은 **문자열 + 어휘 설명**(미등록 술어 허용, Repair 가 정규화). 어휘가 안정되면 v2 에서 enum + `custom` 으로 전환.
- 추가 필드(모델 전용, IR 에는 저장하지 않음): `confidence`(0~1), `evidence`(브리프에서 근거가 된 문장). 보고서로 보낸다.
- strict 모드 요구사항(모든 필드 required, `additionalProperties: false`)을 만족하도록 생성.

### 5.3 프롬프트 (`adapters/structurer/llm/prompt.py`)

- 시스템: 역할, 출력 형식, 스키마 규칙(0 ms 시작·연속 장면, 객체는 ≥1 장면 등장, 관계 규칙), 어휘 목록, 장면 수 상한, **근거 없는 조건을 만들지 말 것**(AC-14 분모 보호).
- few-shot: 카페 숏폼 예시(현 `CAFE_PROMPT` 와 같은 내용의 자연어 버전 → IR), 1~2개 더(제품 광고, 인물 브이로그). 한국어·영어 각 1개.
- 사용자: 목표 길이, 참조 이미지 목록(객체 힌트), 브리프 원문.
- 키 규칙: `entity_key`/`scene_key` 는 영문 snake_case(식별자), 설명·표시 이름은 브리프 언어. 화면 라벨("컵 01")을 위해 `display_name` 을 IR 에 추가하는 것을 권장(9장).

### 5.4 결정성·캐시·비용

- temperature 0, (지원 시) seed 고정. 같은 브리프·모델·프롬프트 버전 → 같은 출력이 목표.
- 캐시 키 = sha256(모델, 프롬프트 버전, 스키마 버전, 브리프). `ArtifactStore` 에 raw 응답 저장(`structuring/{hash}.json`) → 디버깅·재현.
- 자기수정 재시도 최대 2회. 실패 시 마지막 오류를 `error_message` 에 남긴다.
- 비용 상한: 브리프 길이 제한(예: 2,000자), 장면 수 상한.

---

## 6. 보정(Repair) 규칙 (`adapters/structurer/repair.py`)

모델 출력에서 자주 틀리는 것을 결정적으로 고친다. 각 규칙은 **자동 수정(repair) / 경고(warning) / 오류(error)** 중 하나로 보고된다.

| 규칙 | 동작 | 수준 |
|---|---|---|
| 술어 정규화 | 어휘로 정규화, 미등록 술어는 그대로 두고 경고 | repair / warning |
| 관계 값 보정 | 관계 술어의 `expected_value` 를 `related_entity_key` 로 맞춤 | repair |
| 키 생성 | 빈 `scene_key`/`entity_key` → `scene_01`, `entity_01` 순번 부여, 공백·대문자 → snake_case | repair |
| 장면 정렬·스냅 | 시작 시각 기준 정렬, 빈 구간은 앞 장면 연장, 겹침은 뒤 장면 시작을 앞 장면 끝으로 이동, 첫 장면 0 ms | repair (이동량 ms 를 보고) |
| 길이 맞춤 | 합계가 목표 길이와 오차 밖이면 비례 스케일(100 ms 단위 반올림) | repair |
| 객체 연결 | 요구조건이 참조하는 객체가 장면 `entity_keys` 에 없으면 연결 추가 | repair |
| 미등장 객체 | 어떤 장면에도 없는 객체 → 삭제하고 경고 | warning |
| 빈 장면 | 객체가 하나도 없는 장면 → 경고(장면 수준 조건만 있는 경우 허용) | warning |
| 중복 요구조건 | 같은 키가 둘 이상 → 마지막 것만 남기고 경고 | warning |
| 근거 없는 조건 | `evidence` 가 비어 있거나 브리프에 없는 값(색·문구)을 쓰면 경고(`unsupported`) | warning |
| 객체 0개 / 장면 0개 | 복구 불가 | error |

규칙은 순서가 있는 함수 목록으로 구현하고(각각 순수 함수, 단위 테스트), 적용 결과를 `RepairLog(rule, target, before, after)` 로 남긴다.

---

## 7. 구조화 보고서와 사용자 확인 루프

### 7.1 `StructuringReport` (도메인 값 객체, `domain/structuring.py`)

```
StructuringReport
  source: "prompt" | "ir" | "dsl"
  model: {provider, name, prompt_version, schema_version} | null
  repairs: [RepairLog]          # 자동으로 고친 것
  warnings: [Issue(code, message, target_key?)]   # 사람이 확인할 것
  errors:   [Issue]             # 실패 원인 (FAILED 일 때)
  confidence: {entity_key|requirement_key -> 0~1}  # 모델이 준 값, 없으면 비움
  evidence:   {requirement_key -> 브리프 문장}
  raw_uri: 산출물 저장소의 원본 응답 URI | null
```

저장: `video_versions.structuring_report_json` (ERD 확장 컬럼). 조회: `GET /api/versions/{id}` 응답에 `structuring_report` 포함.

### 7.2 확인·수정 API (리스크 2 대응)

| Method | Endpoint | 동작 |
|---|---|---|
| GET | `/api/versions/{id}` | IR + `structuring_report` (경고 항목에 `target_key` 가 있어 화면이 블록을 강조할 수 있음) |
| PUT | `/api/versions/{id}/ir` | DRAFT 에서만 허용. 본문의 IR 문서를 Repair → validate 후 저장, 새 보고서 반환. QUEUED 이후는 409 `VERSION_NOT_EDITABLE` |
| POST | `/api/versions/{id}/restructure` | 같은 브리프로 구조화를 다시 돌린 **새 버전** 생성(모델/프롬프트 변경 비교용). 원본 보존 |

흐름: 브리프 입력 → (비동기) DRAFT + 보고서 → 화면이 경고 블록 표시 → 사용자가 블록 수정(`PUT /ir`) → 경고 0 또는 확인 → `POST /generations`.
생성 접수 시 조건이 고정되므로 "확인한 IR 로 생성했다"는 사실이 `frozen_fingerprint` 로 남는다.

---

## 8. 참조 이미지 그라운딩 (아이디어 10, Sprint 2)

- 새 포트 `ImageAttributeExtractor.extract(image_uri, entity_hint) -> [(predicate, value, confidence)]` (VLM: GPT-4o 계열, Qwen-VL 로컬 등).
- 참조 이미지에 `entity_key` 가 지정된 경우만 그 객체의 속성(color, material, text, shape)을 뽑아 **브리프에 없는 조건을 보충**한다. provenance 를 `reference_image` 로 표시.
- 브리프와 충돌(브리프 "빨간 컵", 이미지 "파란 컵")하면 브리프를 우선하고 경고 `conflict_with_reference` 를 남긴다.
- 전역 스타일 이미지(`entity_key` 없음)는 장면 수준 조건(lighting, mood) 후보로만 제안한다.

---

## 9. 스키마 확장 제안

| 제안 | 내용 | 영향 |
|---|---|---|
| `EntitySpec.display_name` | 화면 라벨("컵 01", "바리스타")을 키와 분리 | **채택 (ADR-0022)**: IR·API·DB 열. 생성 조건이 아니라 fingerprint·생성 지시문에 넣지 않는다. LLM 구조화는 아직 채우지 않는다 |
| `StructuringReport` 저장 | 7장 | `video_versions` 컬럼 1개 |
| 요구조건 단위 시간 구간 | `RequirementSpec.start_ms/end_ms?` (장면 내부 구간, "2초부터 로고") | 검수·전파 규칙 확장 필요 → schema_version 2 로 미룸 |
| 술어별 값 형식 | COLOR 는 색 이름 또는 `#RRGGBB`, POSITION 은 `left/center/right/top/bottom` enum | 검수기 구현 시 함께 확정 |

---

## 10. 평가 (골드셋)

- `tests/evaluation/structuring_gold/*.json`: 브리프(한국어 10, 영어 5 이상) + 기대 IR. 카페·제품·인물·문구(TEXT)·관계 포함.
- 지표 (`evaluation/structuring_metrics.py`, 순수 함수):
  - 객체 재현율/정밀도 (키 매칭은 `display_name`/설명 유사도로 정렬 후 비교)
  - 요구조건 F1 (키 + 값 일치, 값은 정규화 후 비교)
  - 장면 경계 오차 (평균 |Δms|) 와 장면 수 일치율
  - 보정 횟수(낮을수록 모델이 규칙을 잘 따름), 경고 수
- 목표치(초안): 객체 재현율 ≥ 0.9, 요구조건 F1 ≥ 0.8, 장면 수 일치 ≥ 0.8. T2/AC-11 판정 근거로 기록.
- 실행: `pytest -m slow tests/evaluation` (실제 모델), CI 는 `ReplayLLMClient` fixture 로 파이프라인 회귀만 검증.

---

## 11. 모듈·파일 계획과 작업 순서 (TDD)

```
src/frame/application/ports/llm.py                 LLMClient 포트, LLMError
src/frame/application/ports/structurer.py          StructuringResult(ir, report) 로 반환형 확장
src/frame/domain/structuring.py                    StructuringReport, Issue, RepairLog
src/frame/adapters/structurer/llm/schema.py        IR JSON Schema 생성
src/frame/adapters/structurer/llm/prompt.py        시스템/사용자 프롬프트, few-shot, 프롬프트 버전
src/frame/adapters/structurer/llm/clients/         fake(replay) · openai · anthropic · vllm
src/frame/adapters/structurer/repair.py            보정 규칙(순수 함수 목록) + RepairLog
src/frame/adapters/structurer/llm_structurer.py    Prepare→Extract→(Ground)→Repair→Validate→Report
src/frame/adapters/structurer/passthrough.py       IR 직접 입력 모드 (Repair→Validate 만)
src/frame/application/use_cases/edit_draft_ir.py   PUT /ir
src/frame/evaluation/structuring_metrics.py        골드셋 지표
tests/unit/structurer/test_repair_rules.py         규칙별 단위 테스트 (가장 먼저)
tests/unit/structurer/test_schema.py               스키마가 IR.to_dict 와 일치하는지 (왕복)
tests/contract/test_structurer_contract.py         LLMStructurer(ReplayLLMClient) 를 params 에 추가
tests/evaluation/test_structuring_gold.py          slow, 실제 모델
```

| 순서 | 작업 | 공수(h) | 비고 |
|---|---|---|---|
| 1 | `StructuringReport` + 포트 반환형 확장 + 유스케이스 저장/API 노출 | 4 | 기존 Fake/DSL 은 빈 보고서 반환 |
| 2 | Repair 규칙 (테스트 먼저) | 6 | 모델 없이 완성 가능 |
| 3 | JSON Schema 생성 + 왕복 테스트 | 3 | |
| 4 | `LLMClient` 포트 + `ReplayLLMClient` + `LLMStructurer` 조립 (fixture 로 초록) | 5 | 자기수정 재시도 포함 |
| 5 | IR 직접 입력 모드 (`POST /projects {ir}`) + `PUT /ir` | 5 | 프로토타입 ① 화면 연결 |
| 6 | 실제 클라이언트 1개 (팀 선택) + 골드셋 10건 + 지표 | 8 | `slow` 마커 |
| 7 | 참조 이미지 그라운딩 (VLM) | 8 | Sprint 2 |

팀이 Sprint 1 에서 T2 로 만든 구조화 코드(노트북 등)가 있다면 `RequirementStructurer` 어댑터로 감싸 계약 테스트부터 통과시킨 뒤, 위 Repair·보고서를 그 위에 얹는다.

---

## 12. 리스크와 결정 필요 사항

| 리스크 | 대응 |
|---|---|
| 모델이 브리프에 없는 조건을 만들어 AC-14 분모를 부풀림 | `evidence` 요구 + `unsupported` 경고 + 사용자 확인 루프 |
| 장면 과분할/과병합 | 장면 수 상한·하한 힌트, 길이 스냅, 골드셋 장면 수 지표 |
| 한국어 키·영어 키 혼용 | 키는 영문 snake_case 고정, `display_name` 으로 라벨 분리 |
| API 비용·지연 | 캐시, 브리프 길이 제한, 비동기 작업(이미 있음) |
| 참조 이미지 외부 전송(개인정보) | VLM 사용 시 동의·마스킹, 로컬 VLM 옵션 |
| 재구조화로 키가 바뀌어 검수 결과 비교 불가 | 재구조화는 새 버전으로만, 수정은 `POST /revisions` 로만 |

팀이 정해야 할 것: (1) 추출 모델 — API(OpenAI/Anthropic) 또는 로컬 vLLM, (2) 화면 입력 방식 — 자연어 우선인지 블록 우선인지(둘 다 지원하되 기본값), (3) ~~`display_name` 스키마 확장 수용 여부~~ → 채택 (ADR-0022), (4) 골드셋 작성 담당과 건수.
