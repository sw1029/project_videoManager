# ADR-0019 변경 계획 미리보기와 글로 쓴 변경 요청의 해석

- 상태: 채택 (2026-11-12)
- 관련: US-01 (AC-02), US-04 (화면 연동), ADR-0009·0014·0017·0018
- 검토 기록: [docs/research/concept-ux-review.md](../research/concept-ux-review.md)

## 맥락
- 컨셉 화면의 '② 변경 요청' 은 자연어다: "컵 색상만 청록색으로 동일해줘. 인물과 배경은 유지해줘."
  '④ 실행' 의 첫 단계는 '변경 계획 확인' 이고, '① 적용 범위' 는 "모든 등장 구간 → 장면 02 · 03" 처럼 어느 장면이 대상인지 보여 준다.
- 수정 API 는 구조화된 값(객체·술어·새 값)만 받는다. 프론트 개편 브랜치는 "현재 API 에는 객체·속성·새 값만 전달됩니다.
  자유형 요청과 보존 체크는 향후 백엔드 지원이 필요합니다" 로 표시한다.
- 수정 버전을 만들기 전에는 어느 장면이 다시 만들어지는지, 얼마나 걸리고 얼마가 드는지 알 수 없었다.
  생성은 장면당 100~145초·$0.2125(Wan 3.0 480p 4초), 편집은 41~50초·$0.12 다 (1차 실측). 누르기 전에 보여 줘야 한다.
- 객체 카드와 객체별 전후 비교에 쓸 이미지가 없었다. 정체성 기준 이미지(ADR-0015)는 내부 저장소 URI 만 있고 HTTP 경로가 없다.

## 결정

### 1. `POST /api/versions/{version_id}/revision-plans` — 계획만 돌려준다 (상태는 바뀌지 않는다)
- 입력: 구조화된 값(`predicate` + `new_value`, 필요하면 `entity_key`·`scope`·`scene_keys`·`current_value`·`kind`·`keep`) 또는
  `request_text` 만. 둘 다 없으면 422.
- 출력
  - `kind`: 값이 달라지면 CHANGE, 이미 그 값이면 REPAIR 로 **스스로 정한다** (`kind` 를 주면 그 종류로만 본다).
  - `target_scene_keys` 와 `scenes[]`: 장면마다 다시 만드는지(`will_regenerate`), 지금 그 조건의 값(`current_value`),
    이 버전에서 그 요구조건의 검수 판정(`verdict`), 그 장면을 지난번에 만들 때의 비용·시간(`recorded_cost_usd`·`recorded_seconds`).
  - `estimate`: 다시 만들 장면의 지난 기록을 더한 어림값과, 기록이 있는 장면 수.
  - `revision_request`: **이 계획을 실행하려면 `POST /revisions` 에 그대로 보내는 본문.**
  - `interpreted`·`notes`: 글을 해석해 만든 계획인지와, 어떻게 읽었는지 한 문장.
- 수정 요청을 푸는 규칙(`resolve_revision`)은 수정 버전 생성(ADR-0017)과 같은 함수를 쓴다. 계획과 실행이 어긋나지 않는다.

### 2. 글로 쓴 변경 요청의 해석 (`ChangeRequestInterpreter`, `FRAME_CHANGE_INTERPRETER`)
- `request_text` 만 오면 해석기가 구조화된 변경 하나와 유지할 요소로 옮긴다. 화면에서 고른 객체는 해석의 실마리로 넘기고,
  화면에서 고른 범위·장면·유지 요소가 있으면 해석 결과보다 우선한다.
- `rule_based`: `cup.color = teal @ 장면, 장면; keep: barista.action, cup.shape, lighting, 배경` 형식. LLM 없이 돈다.
- `llm`: 자연어. 지금 버전의 객체·장면·요구조건과 술어 어휘를 주고 JSON 으로 받는다.
  - 객체·장면·유지 요소는 **이 영상에 있는 것만** 받아들인다. 없는 것은 버리고 `notes` 에 적는다.
  - 객체 없이 '모습' 을 유지하라는 답은 배경으로 읽는다 (실제 모델이 "배경은 유지해줘" 를 그렇게 돌려줬다).
  - 읽을 수 없으면 422 `CHANGE_REQUEST_NOT_UNDERSTOOD`.
- `auto`(기본)는 구조화기를 따른다: `FRAME_STRUCTURER=llm` 이면 `llm`, 아니면 `rule_based`.
  `/health` 의 `adapters.change_interpreter` 로 화면에 알려 준다.
- 해석 결과는 계획에만 쓴다. 실제 수정은 사용자가 계획을 확인한 뒤 구조화된 값(`revision_request`)으로 요청한다.
  요청 원문은 수정 버전에 기록으로 남기고(`revision.request_text`) 생성에는 쓰지 않는다.
- 요청을 처리하다 부른 LLM 의 오류: 연결·인증·시간 초과·요청 제한은 503, 응답 형식 위반은 502. `error_code` 는 LLM 오류 코드 그대로다.

### 3. 참조 이미지의 HTTP 경로
- `GET /api/versions/{version_id}/reference-images/{reference_image_id}` 와 `reference_images[].image_url`.
  저장소에 있는 이미지만 내려 준다 (정체성 기준 이미지가 그렇다). 저장소 밖의 주소(사용자가 준 외부 참조)는 `image_url` 이 null 이다.
- 버전에 딸린 이미지만 조회하므로 임의 경로 접근은 불가능하다 (클립·영상과 같은 방식).
- `reference_images[].is_current`: 그 기준이 이 버전의 조건과 맞는가. 수정으로 객체의 모습이 바뀌면 부모에게서 물려받은
  예전 기준(false)과 새로 뽑은 기준(true)이 함께 남는다. 예전 기준은 생성에 쓰이지 않지만 응답만으로는 가릴 수 없었다.
  객체 카드의 썸네일은 true 인 것을, 객체별 전후 이미지는 false(전)와 true(후)를 쓴다. 사용자 참조는 늘 true 다.

### 4. 장면마다 클립이 어떻게 만들어졌는지 (`ir.scenes[].clip_production`)
- `method`(GENERATED | EDITED)·`model`·`cost_usd`·`seconds`·`edit_fallback_reason`. 생성기가 클립 메타데이터에 남기는 공통 기록
  (`model`·`cost_usd`·`generation_seconds`·`edit`)에서 옮긴다. 기록이 없으면 null 이다.
- 계획은 실행 전의 어림이고, 이것은 실행 뒤의 사실이다. 편집하려던 장면이 새로 생성됐으면 화면이 사유를 보여 줄 수 있다
  (실호출: "부모 작업의 저장본 주소를 얻지 못함").

## 결과
- 화면은 요청 문장(또는 고른 값)을 `revision-plans` 에 보내 계획을 보여 주고, 확인을 누르면 `revision_request` 를
  `revisions` 에 보낸 뒤 `generations` 를 부른다. 해석·검증·범위 계산을 화면이 따로 하지 않는다.
- 객체 카드의 썸네일과 객체별 전후 이미지는 버전마다의 기준 이미지(`origin=DERIVED`, `entity_key`, `source_scene_key`)로 만들 수 있다.
- 한계
  - 어림값은 지난 생성 기록의 합이다. 편집으로 만들면 실제는 더 적다 (2차 실호출: 어림 $0.425·225초, 실제 FLUX 편집 $0.24·92초).
    **편집으로 될지 새로 생성될지도 계획은 알려 주지 않는다.** 방금 만든 영상은 부모 작업의 저장본 주소가 아직 없어
    새로 생성될 수 있다 (ADR-0016). 생성기가 방식과 어림값을 주는 포트가 다음 과제다 (연구 문서 §7 B1).
  - 한 문장에 변경이 둘 이상이면 읽지 못한 것으로 돌려준다 (수정 하나 = 요구조건 하나, ADR-0017).
  - 해석은 LLM 한 번의 답이다. 화면이 계획을 보여 주고 확인받는 것이 전제다.
  - 추천 칩(색상 변경·질감 변경)은 따로 주지 않는다. `GET /api/vocabulary/predicates` 의 술어와 그 객체의 요구조건으로 화면이 만든다.

## 대안
- `POST /revisions` 가 글을 직접 받기: 해석이 틀리면 원치 않는 수정 버전이 생기고 곧바로 유료 생성으로 이어질 수 있다.
  미리보기와 실행을 나눴다.
- 수정 버전(DRAFT)을 먼저 만들고 그것을 계획으로 쓰기: 지금도 가능하지만 확인 전에 버전이 쌓이고, 취소할 방법(버전 삭제)이 없다.
- 해석을 화면에서(브라우저가 LLM 호출): 키가 브라우저에 가야 하고 IR·어휘 검증을 다시 구현해야 한다. 기각.

## 검증
- 단위·API 테스트 (네트워크 없이): `tests/unit/test_change_request_interpreters.py`(규칙 기반·LLM 해석), `tests/api/test_revision_plan_api.py`
  (계획 → 확인 → 수정 버전, 화면에서 고른 값의 우선, 오류 코드), `tests/api/test_api_flow.py`(참조 이미지 경로), `tests/unit/test_identity_config.py`.
- 실호출 (2026-11-12, 연구 문서 §5)
  - "컵 색상만 청록색으로 동일해줘. 인물과 배경은 유지해줘." 를 gpt-4.1-mini 가 1.2~2.3초에 (cup, color, teal) 로 읽었고,
    요구가 이미 청록이라 REPAIR 로 정해졌다. 장면별 판정(FAIL·FAIL)과 어림값이 함께 나왔다.
  - 첫 실행에서 "배경은 유지" 가 객체 없는 APPEARANCE 로 와서 빠졌다. 보정한 뒤의 실행에서는
    [인물의 모습·동작, 배경] 으로 읽혔다.
  - 계획의 `revision_request` 에 화면의 유지 요소 5개를 넣어 그대로 실행했다. 기준 이미지 경로는 200(image/jpeg)이었다.
  - 컨셉 예시(4장면)에서는 CHANGE 로 읽었다: (red_mug, color, teal), 대상 장면 2·3. 화면에서 유지 요소를 고르지 않고
    글에서 읽은 것만으로 실행해도 편집 지시문에 `Do not change: ...` 가 들어갔다 (4회차).
  - 색을 바꾼 수정 버전의 참조 이미지: 'red ceramic mug' 는 `is_current=false`, 'teal ceramic mug' 는 `true` (5회차).
  - 주소를 받지 못해 새로 생성된 장면의 `clip_production`: `method=GENERATED`,
    `edit_fallback_reason="부모 작업의 저장본 주소를 얻지 못함 (6회 조회: …)"` (5회차).
