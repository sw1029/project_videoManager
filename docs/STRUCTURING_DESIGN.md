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
| `StructureRequirements` | 비동기, FAILED 기록, 목표 길이 오차 | 보고서 저장 없음, IR 직접 입력 경로 없음 |
| API | `POST /projects {prompt}` | `ir` 직접 입력 없음, DRAFT IR 수정 없음, 보고서 조회 없음 |
| 평가 | 지표 1 하네스(수정 실험), 구조화 골드셋 15건·지표·결과 기록 (10장) | 첫 실제 실행에서 요구조건 F1 0.763 으로 목표 0.8 미달 (10.6) |

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
| `EntitySpec.display_name` | 화면 라벨("컵 01", "바리스타")을 키와 분리 | IR·API·DB 컬럼 추가, 작음 |
| `StructuringReport` 저장 | 7장 | `video_versions` 컬럼 1개 |
| 요구조건 단위 시간 구간 | `RequirementSpec.start_ms/end_ms?` (장면 내부 구간, "2초부터 로고") | 검수·전파 규칙 확장 필요 → schema_version 2 로 미룸 |
| 술어별 값 형식 | COLOR 는 색 이름 또는 `#RRGGBB`, POSITION 은 `left/center/right/top/bottom` enum | 검수기 구현 시 함께 확정 |

---

## 10. 평가 (골드셋)

> 상태 (2026-11-14, 이슈 #17): 골드셋 15건, 지표, 리플레이 검증, 결과 기록·비교 형식을 만들었다.
> 사용자 승인으로 gpt-4.1-mini·프롬프트 2026-11-12.1 로 한 번 돌렸다. 객체 재현율 0.96 과 장면 수 일치 1.0 은 목표를 넘었고,
> 요구조건 F1 0.763 은 목표 0.8 에 못 미쳤다 (10.6).

### 10.1 골드셋

`tests/evaluation/structuring_gold/*.json`, 한국어 10건·영어 5건. 파일 하나에 브리프·목표 길이·주석을 붙인 기대 IR 이 들어 있다.

- 담은 것: 관계(holds·wears·near·looks_at·on), 화면 문구(text·brand), 동작(action), 장면 수준 조건(장소·조명·분위기·카메라·시간대·날씨),
  동물·수량·위치, 길이가 다른 장면(en-02, 6/5/4초), 장면마다 초를 적지 않은 브리프(en-03·en-05), 객체 없이 풍경만 있는 장면(ko-09 장면 1).
- 컨셉 화면의 카페 브리프를 ko-01 로 넣었다 (16초·4장면, 컵은 장면 2·3).
- 작성 규칙은 구조화 프롬프트(`llm_prompt.py`)의 규칙과 같다.
  1. 키는 그것이 무엇인지(`cup`)로 짓고, 모습은 요구조건(`color = red`)으로 적는다. 값은 짧은 영어 소문자 구로 쓰고, 문구·브랜드는 철자 그대로 쓴다.
  2. 객체의 속성은 그 객체가 나오는 모든 장면에 되풀이하고, '유지'·'내내' 같은 조건은 모든 장면에 둔다.
  3. 소품(책상·테이블·러닝머신)은 객체이고, 놓인 자리는 `on` 관계로 적는다. `location` 에는 장소만 쓴다.
     실호출에서 본 `location = wooden table`(이슈 #17)을 재기 위한 규칙이다 (ko-08 등).
  4. 브리프가 말한 것은 필수, 짐작만 되는 것은 `optional` 로 둔다. 말하지 않은 것은 적지 않으므로, 모델이 내면 오탐이 된다.
  5. 기대 값에는 핵심 낱말만 적는다(`warm`). 같은 뜻의 다른 표현은 `accept`, 다른 술어는 `accept_predicates` 로 받는다.
- 주석 필드는 `IR.from_dict` 가 읽지 않는다.
  - 객체: `aliases`(키·설명에서 찾을 이름, 한국어·영어), `accept_kinds`, `optional`
  - 장면: `optional_entities`
  - 요구조건: `accept`, `accept_predicates`, `optional`
  - 요구조건의 `scene_key` 와 null 필드는 생략할 수 있다.

### 10.2 지표 (`evaluation/structuring/` 패키지)

코드는 `src/frame/evaluation/structuring/` 하위 패키지에 개념별로 나뉘어 있다. 밖에서는 패키지 이름으로만 import 한다 (`from frame.evaluation.structuring import …`).
패키지 `__init__.py` 머리에 맡는 개념·공개 이름·모듈 지도가 있다.

| 모듈 | 맡는 일 |
|---|---|
| `_values.py` | 값 비교 (어휘 정규화 뒤 값 종류별) |
| `_gold.py` | 골드 주석 읽기, 골드 사례 파일·지문 |
| `_matching.py` | 모델 객체·장면을 골드에 짝짓기 |
| `_scoring.py` | 한 사례 채점 (tp/fp/fn, 장면 오차) |
| `_summary.py` | 집계·목표 판정·대표 지표 비교 |
| `_records.py` | 실행·기록·다시 채점·기록 비교 |
| `_report.py` | 사람이 읽는 표 |

키 이름은 모델·프롬프트마다 다르다(`red_mug` / `cup`, `scene_1` / `smile`). 그래서 먼저 키와 무관하게 짝을 맞춘다.
- 객체: 종류가 같고 별칭이 키·설명에 들어 있는 짝만 후보로 본다. 그중 (필수 짝 수, 선택 짝 수, 맞은 별칭 수)가 가장 큰 일대일 대응을 고른다.
  대응을 모두 훑는데, 그 수가 5만을 넘으면 필수 객체부터 가장 잘 맞는 빈 짝을 차례로 고른다 (지금 골드·응답은 최대 192).
  차례로 고르면 가장 좋은 짝을 놓칠 수 있어, 그 사례를 채점 결과·기록(`entity_matching: greedy`)·요약 표에 밝힌다.
- 장면: 모델 타임라인을 골드 길이에 맞춰 늘이거나 줄인 뒤, 시간이 가장 많이 겹치는 골드 장면에 붙인다.

| 지표 | 정의 | 목표(초안) |
|---|---|---|
| 객체 재현율 | 맞춘 필수 객체 / 필수 객체 | ≥ 0.9 |
| 객체 정밀도 | 맞춘 필수 객체 / (맞춘 필수 객체 + 골드에 없는 모델 객체) | — |
| 요구조건 정밀도·재현율·F1 | (장면, 객체, 술어, 상대 객체)가 같고 값이 정규화 뒤 맞으면 맞춤 | F1 ≥ 0.8 |
| 장면 수 일치 | 장면 수가 골드와 같은 사례 / 전체 사례 | ≥ 0.8 |
| 경계 오차 | 장면 수가 같을 때 안쪽 경계(장면 끝 시각)의 평균 \|Δms\| | — |
| 길이 오차 | \|모델 전체 길이 − 목표 길이\|의 평균 | — |
| 등장 장면 F1 (보조) | (장면, 객체) 쌍. 수정 전파 범위가 여기서 정해진다 | — |

- 값은 어휘 정규화(ADR-0008)를 거친 뒤 값 종류별로 견준다.
  - 관계: 상대 객체를 맞춘 것으로 비교가 끝난다.
  - 문구·브랜드: 따옴표와 공백만 정리하고, 철자·대소문자까지 같아야 맞춤이다.
  - 수량: 숫자로 견준다 (three / 세 마리 → 3).
  - 그 밖의 값: 골드 값의 내용어가 모델 값에 모두 들어 있으면 맞춤이다 (`warm` ⊂ `warm cafe lighting`).
    영어 어미(smiling/smiles → smile), 한국어 색 이름(빨간색 → red), 붙여 쓰기(close up / closeup)는 같은 말로 본다.
    동사를 부정하는 말(not·never·n't·안·않·못)이 모델 값에만 있으면 다른 조건으로 본다 (smiling ≠ not smiling).
    `안`·`못` 은 뒤에 낱말이 올 때만 부정으로 본다 (`안 웃는다` 는 부정, `카페 안`·`나무 못` 은 자리·물건).
    `못` 으로 시작하는 낱말은 하다 활용(`못한다`·`못했다`)만 부정이다 (`못생긴`·`못된` 은 꾸미는 말).
  - 알려진 한계: 명사를 부정하는 말(no·without·없)은 보지 않는다 (`walks without umbrella` 는 걷기로 맞다).
    다른 절에 있는 부정도 값 전체를 다른 조건으로 만든다 (`standing, not moving` ≠ standing).
    여러 값이 섞여도 맞춤이다 (골드 red, 모델 `red and blue`). 동의어는 `accept` 로만 받는다.
    필수 항목이 없는 사례만으로는 재현율의 분모가 0 이라 목표를 넘을 수 없다.
- 세는 법
  - 값이 틀린 조건은 오탐 1·놓침 1, 골드에 없는 조건은 오탐 1이다. 정밀도가 근거 없는 조건을 잡아 AC-14 의 분모를 지킨다.
  - 선택 항목은 맞으면 세지 않고(중립), 틀리면 오탐으로 센다.
  - 장면을 쪼개 같은 조건을 되풀이하면 하나로 센다 (쪼갠 것은 장면 수 지표가 잡는다).
  - 장면을 합치면 짝이 없는 골드 장면의 조건을 놓친 것으로 센다.
  - 구조화가 실패한 사례는 필수 항목을 모두 놓친 것으로 센다.
    채점기 자신의 결함(`SCORING_ERROR`)도 실패로 세지만, 오류 이름으로 모델·구조화의 실패와 가른다.
    구조화기 안의 예상 밖 오류(`UNEXPECTED_ERROR`)와 채점기의 결함은 트레이스백을 로그로 낸다.
  - 집계는 모든 사례의 개수를 더한 micro 평균이다.
- 목표치는 초안이다. 첫 실제 실행 결과를 보고 팀이 확정한다.
- 처음 계획한 보정 횟수·경고 수는 아직 지표로 집계하지 않는다. 기록에 사례별 보정 메모만 남기고, 집계는 구조화 보고서(#16) 뒤에 붙인다.

### 10.3 실행

| 무엇 | 명령 | 모델 호출 |
|---|---|---|
| CI | `pytest -m "not slow and not gpu"` (`tests/unit/test_structuring_metrics.py`, `tests/evaluation/test_structuring_gold.py`) | 없음 (`ReplayLLMClient`) |
| 실제 모델 | `FRAME_EVAL_STRUCTURING_LIVE=1 FRAME_LLM_KEY_FILE=<키 파일> pytest -m slow tests/live/test_structuring_gold_live.py -s` | 15건. 첫 실행은 104초, 호출 22회(재시도 7회), 약 $0.03~0.04 (어림, 10.6) |
| 기록 비교 | `python -m frame.tools.evaluate_structuring compare <기준.json> <비교.json>` | 없음. 회귀가 있으면 종료 코드 1, 기록을 읽지 못하면 2 |
| 다시 채점 | `python -m frame.tools.evaluate_structuring rescore <기록.json> [--write]` | 없음. 기록의 원본 응답을 지금 골드로 채점. 골드 폴더가 없거나 비었거나, 골드에 없는 사례가 기록에 있으면 쓰지 않고 종료 코드 2 |

두 도구 명령은 저장소 루트에서 실행한다 (`--gold` 의 기본값이 지금 폴더 기준이다).
출력은 UTF-8 로 맞추므로, 한국어 Windows 에서 출력을 파일·파이프로 보내도 깨지지 않는다.

CI 는 세 가지를 확인한다.
- 골드가 IR 규칙과 프롬프트 규칙을 지키는가
- 골드 자체를 재생하면 모든 지표가 1 이 되는가
- 컨셉 카페 브리프에 대한 gpt-4.1-mini 실호출 결과 3건(`tests/evaluation/replay/`)을 채점한 결과가 기대와 같은가

실제 모델 테스트(`tests/live/`)는 전용 스위치 `FRAME_EVAL_STRUCTURING_LIVE=1` 일 때만 돈다.
- 서버 설정(`FRAME_STRUCTURER`)은 보지 않고 LLM 구조화기로 돈다. 서버를 띄우던 셸에서 실수로 켜지지 않게 하려는 것이다 (감사 B17-01).
- 스위치를 켰는데 키·패키지가 없으면 건너뛰지 않고 설정 오류로 실패한다.
- 구조화기가 원본 응답·시도 횟수를 내주지 않으면(`StructuringRunDetails` 가 아니면) 모델을 부르기 전에 실패한다.
  다시 채점할 수 없는 기록을 남기지 않으려는 것이다.
- 돈 들여 받은 결과부터 기록에 쓰고, 그 뒤에 기준 기록을 읽는다. 기준 기록을 읽지 못하면 비교만 건너뛴다.
- 목표치에 못 미치면 기록을 남긴 뒤 실패한다.

### 10.4 결과 기록과 비교

- 위치: `docs/evaluation/structuring/{프롬프트 버전}__{모델}__{실행 시각}.json`.
  - 실제 실행 테스트는 기본으로 임시 폴더에 쓴다. 테스트가 소스 트리에 저절로 파일을 남기지 않게 하려는 것이다.
  - 저장소에 남길 때만 `FRAME_EVAL_RECORD_DIR=docs/evaluation/structuring` 을 주고, 그 기록을 커밋해 프롬프트 버전별로 쌓는다.
- 내용(`record_version` 1)
  - 구조화기: 이름·제공자·모델·프롬프트 버전·스키마 버전, 응답이 알려 준 모델 이름
  - 골드셋 지문(내용 해시)
  - 대표 지표·목표치·목표 충족 여부·토큰 합계, 언어별 요약
    (토큰은 사례마다 마지막 시도의 값이다. 자기수정 재시도분은 빠지므로 비용은 시도 횟수와 함께 읽는다)
  - 사례별 tp/fp/fn, 놓친 조건·값이 틀린 조건·골드에 없는 조건, 장면 수·경계 오차, 시도 횟수, 보정 메모, **모델의 원본 응답**
  - 기록은 커밋하는 파일이라 접속 정보·로컬 경로를 남기지 않는다.
    오류 문구는 절대 경로를 `<경로>` 로 가리고 300자에서 자른다. 사용자 폴더는 이름에 빈칸이 있어도 통째로 가린다.
    URL 은 경로로 보지 않고 남기되(어느 주소에서 실패했는지 보이게) 계정(`user:pw@`)·질의·조각을 뺀다. 구조화기 정보의 URL 도 같다.
- 비교: 실제 실행 테스트는 기록 폴더(주지 않으면 `docs/evaluation/structuring/`)의 가장 최근 기록과 견준 표를 출력한다.
  지표별 변화와 회귀 표시, 사례별로 새로 놓친 조건과 새로 맞춘 조건이 나온다.
  코드에서는 `compare_records(read_record(a), read_record(b))` 로 견준다.
- 골드셋을 고치면 지문이 달라진다. 비교 표가 같은 잣대가 아니라고 경고하고, 다시 채점하는 명령을 알려 준다.
  이때는 옛 기록을 `evaluate_structuring rescore --write` 로 지금 골드에 다시 채점한다.
  - 모델을 다시 부르지 않는다. 실행 정보(시각·모델·시도·토큰)는 그대로 두고 `rescored` 가 붙는다.
  - `rescored` 에는 다시 채점한 시각과 실제 실행 때 골드의 지문이 있다. 여러 번 다시 채점해도 실행 때 지문을 지킨다.
  - 실행 때 실패한 사례는, 남은 응답을 재생해도 실패하면 실행 때의 오류를 그대로 둔다 (재생은 1회 시도라 문구가 달라진다).
  - 기록에는 있는데 골드에 없는 사례가 있으면 다시 채점하지 않는다. 돈 들여 받은 응답을 조용히 지우지 않으려는 것이다.
- 한쪽 기록에만 있는 사례가 있으면, 비교 표가 그 사례를 밝히고 대표 지표가 서로 다른 사례 집합의 값이라고 적는다.
- 프롬프트를 바꿀 때 순서
  1. `PROMPT_VERSION` 을 올린다.
  2. `FRAME_EVAL_RECORD_DIR=docs/evaluation/structuring` 을 주고 실제 실행 테스트를 돌린다 (사용자 승인 뒤, 비용 발생).
  3. 비교 표에서 회귀를 확인한다.
  4. 기록을 커밋한다.

### 10.5 리플레이로 본 것 (컨셉 카페 브리프)

concept-ux 2차 e2e(2026-11-12)에서 gpt-4.1-mini 가 ko-01 브리프를 구조화한 결과 3건을 다시 채점했다.

| 실행 | 프롬프트 | 객체 키 | 요구조건 | 놓친 조건 |
|---|---|---|---|---|
| 3회차 | 2026-11-07.2 | barista · red_mug | tp 17 · fp 0 · fn 1 | 장면 2 의 컵 색 (클로즈업에만 적었다) |
| 4회차 | 2026-11-12.1 | barista · cup | tp 17 · fp 0 · fn 1 | 장면 1 의 '카메라를 보며' |
| 5회차 | 2026-11-12.1 | barista · cup | tp 17 · fp 0 · fn 1 | 장면 1 의 '카메라를 보며' |

- 세 결과 모두 객체 2/2, 장면 4/4, 경계 오차 0, 요구조건 F1 0.971 이다.
- 키는 달라도(`red_mug` / `cup`) 같은 객체로 맞췄다.
- 키 규칙을 고친 프롬프트는 컵 색을 두 장면 모두에 적었다. 4·5회차는 장면 1 의 시선 조건을 뺐다.
- 브리프 하나뿐이라 일반화할 수는 없다. 15건 전체는 실제 실행으로 본다 (10.6).

### 10.6 첫 실제 실행 (2026-11-14, gpt-4.1-mini, 프롬프트 2026-11-12.1)

기록: `docs/evaluation/structuring/2026-11-12.1__gpt-4.1-mini__20261114T113631.json` (응답 모델 gpt-4.1-mini-2025-04-14).
- 104초 걸렸고, 호출은 22회(15건 + 자기수정 재시도 7회)였다.
- 기록된 토큰은 입력 26,801·출력 9,258 이다 (마지막 시도분만). 1M 토큰당 입력 $0.40·출력 $1.60 으로 치면 약 $0.026 이다.
  재시도 7회의 토큰은 기록되지 않았다. 호출 한 번의 평균만큼으로 치면 모두 약 $0.037 이다 (어림이다. 실제 청구액은 확인하지 않았다).
- 실행한 그대로의 점수는 그 기록의 첫 커밋에 있다. 골드를 보완한 뒤 같은 응답을 다시 채점해 파일을 고쳐 썼다 (아래 표의 오른쪽 열).

| 지표 | 실행 그대로 | 골드 보완 뒤 다시 채점 | 목표 |
|---|---|---|---|
| 객체 재현율 | 0.962 | 0.962 | ≥ 0.9 ✓ |
| 객체 정밀도 | 0.806 | 0.847 | — |
| 요구조건 정밀도 / 재현율 | 0.799 / 0.701 | 0.830 / 0.706 | — |
| 요구조건 F1 | 0.746 | **0.763** | ≥ 0.8 ✗ |
| 등장 장면 F1 | 0.908 | 0.924 | — |
| 장면 수 일치 / 경계 오차 | 1.0 / 0 ms | 1.0 / 0 ms | ≥ 0.8 ✓ |

장면 나누기와 객체 찾기는 거의 다 맞췄다. 요구조건은 놓친 것(55건)이 골드에 없는 것(27건)보다 두 배 많다.
놓친 것 55건을 사례별로 읽어 나누면 대부분 프롬프트가 이미 적어 둔 규칙을 모델이 지키지 않은 것이다. 이슈 #18(프롬프트·보정)의 우선순위 근거로 쓴다.

| 유형 | 놓침 | 예 | 어긴 프롬프트 규칙 |
|---|---|---|---|
| 속성·장면 조건을 뒤 장면에 되풀이하지 않음 | 12 | 고양이 색·시계 색·컵 색, '유지' 조명 | "Repeat a condition in every scene where it must hold" |
| 모습·문구를 키·설명에만 둠 | 8 | `glass_cup`·`wooden_table`·`yellow_backpack`, 로고의 'FIT ZONE' | 모습은 요구조건, 키에는 모습을 넣지 않음 |
| 옷을 객체로 만들고 `wears` 로 이음 | 6 (+ 조건 오탐 4·객체 오탐 4) | `red_hoodie`·`navy_coat`·`apron`·`red_windbreaker` | outfit 은 요구조건 |
| 장소를 쓰지 않음 / 배경 객체에 붙임 | 6 | 꽃집·헬스장 장소 없음, `bus_stop.location` | 장면 수준 `location`, entity null |
| 자세·화면 위치를 빠뜨리거나 position 을 잘못 씀 | 5 | 문구 위치 없음, `traveler.position = standing on …` | — |
| 소품을 객체로 만들지 않음 (장소로 씀) | 4 (+ 조건 오탐 1) | `location = marble table`, 책상 없음 | 소품은 객체, 장소에는 장소만 (이슈 #17 의 관찰과 같음) |
| 카메라·드론을 객체로 만듦 | 3 (+ 조건 오탐 6·객체 오탐 3) | `looks_at camera`, `drone.action` | 브리프에 없는 객체를 만들지 않음 |
| 주된 동작이 빠지고 `holds` 만 남음 | 3 | 꽃다발 포장·차 따르기·봉투 건네기 | 동작은 action |
| 그 밖 | 8 | 조명을 장소 꾸밈말로 씀(`bright kitchen`), 카메라 지시를 동작으로 씀, 수량을 객체 셋으로 씀 | — |

- 골드에 없는 조건 27건: 위 유형의 짝(`wears`·`looks_at camera`)이 가장 많다. 그 밖에 브리프에 없는 조건을 지어낸 것도 있다.
  - 예: `location = studio`·`lighting = bright` 를 지어냄, 보드를 타면서 `holds skateboard`, 봉투 장면의 서로 바라보기
- 같은 프롬프트라도 실행마다 구조가 달라진다. ko-01 은 2차 e2e 의 두 번(F1 0.971)과 달리 이번에는 `camera` 객체를 만들어 0.895 였다.
  한 번의 실행으로 작은 차이를 판정하지 말고, 회귀가 의심되면 여러 번 돌려 본다.

골드 보완 (실행 뒤, 사례 notes 에도 적음)
- 원칙: 프롬프트가 허용하는데 골드가 예상하지 못한 표현만 받는다.
- 프롬프트가 명시한 규칙을 어긴 것은 그대로 틀린 것으로 둔다. outfit·되풀이·entity null·소품·지어낸 객체가 여기에 든다.

| 사례 | 받기로 한 것 |
|---|---|
| en-04 | '시계를 확인한다' 를 `looks_at` 시계로 쓴 것 (동작은 jogs 만 요구) |
| ko-03 | 던지기 전 `owner holds ball` (선택) |
| ko-05 | 계란을 넣는 `hand near pot` (선택) |
| ko-09 | 해안 산책로 배경 객체 (선택) |
| en-05 | 네온사인 객체, 두 친구의 `near` (선택, 네온 조명은 필수 그대로) |
| ko-08 | '화면' 의 문구를 TEXT 객체로 쓴 것 (선택, 노트북의 text 는 필수 그대로) |

### 10.7 남은 질문

- 목표치: 요구조건 F1 0.8 을 그대로 둘지. 지금 차이의 대부분은 프롬프트 규칙을 지키지 않은 것이라 #18 로 줄일 수 있다.
- 재시도 7회의 거절 사유는 기록되지 않는다 (`LLMStructurer` 가 마지막 오류만 메시지에 담는다). 기록에 남기면 보정 규칙(6장)의 근거가 된다.
- 장소와 소품의 경계: 카페 카운터는 선택 객체로 두고 장소(카페)로도 받았다. 책상·테이블·러닝머신은 객체로 못박았다. 팀 합의가 필요하다.
- 착용물(모자·안경)을 `outfit` 으로 볼지, 객체 + `wears` 로 볼지 프롬프트가 정하지 않았다. 그래서 골드에서는 피했다 (제품인 시계·러닝화만 `wears`).
- 값 비교는 낱말 단위라 동의어는 `accept` 로만 받는다. 의미 유사도(임베딩)를 쓰려면 패키지 설치 승인이 필요하다.
- 골드에 사례를 더할 때: 옛 기록은 다시 채점해도 새 사례가 없고(실행하지 않았다) 지문이 새 기록과 계속 다르다.
  비교 표는 한쪽에만 있는 사례를 밝히지만, 대표 지표는 서로 다른 사례 집합의 값이다.
  사례를 더할 때 사례별 지문과, 두 기록에 다 있는 사례만으로 낸 지표를 기록·비교에 더한다 (델타 감사 B17-D09).

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
src/frame/evaluation/structuring/                  골드셋 지표·실행·기록 (10.2 의 모듈 표)
tests/unit/structurer/test_repair_rules.py         규칙별 단위 테스트 (가장 먼저)
tests/unit/structurer/test_schema.py               스키마가 IR.to_dict 와 일치하는지 (왕복)
tests/contract/test_structurer_contract.py         LLMStructurer(ReplayLLMClient) 를 params 에 추가
tests/evaluation/test_structuring_gold.py          골드 규칙·리플레이 채점 (CI)
tests/live/test_structuring_gold_live.py           slow, 실제 모델 (FRAME_EVAL_STRUCTURING_LIVE=1 로만 켜짐)
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

팀이 정해야 할 것: (1) 추출 모델 — API(OpenAI/Anthropic) 또는 로컬 vLLM, (2) 화면 입력 방식 — 자연어 우선인지 블록 우선인지(둘 다 지원하되 기본값), (3) `display_name` 스키마 확장 수용 여부, (4) 골드셋 작성 담당과 건수.
