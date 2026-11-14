# ADR-0017 수정 의도: 다시 맞추기(REPAIR)와 그대로 둘 요소

- 상태: 채택 (2026-11-12)
- 관련: US-01 (AC-01·02), 성공 지표 1 "수정 성공률 80% 이상, 지정하지 않은 대상의 변경률 10% 이하", ADR-0003·0013·0015·0016
- 검토 기록: [docs/research/concept-ux-review.md](../research/concept-ux-review.md)

## 맥락
- 문제정의서의 컨셉 화면("FRAME 영상 생성 파이프라인 — 컨셉 UI · 예시 데이터")의 '컵 01 수정' 패널은 네 단계다:
  ① 적용 범위 ② 변경 요청 ③ 유지할 요소(인물·동작 / 배경·조명 / 컵 형태) ④ 실행(변경 계획 확인 → 해당 구간 재생성 → 완료 후 재검수).
  패널에는 "원문 : 청록색 컵" 이 붙어 있고, 전후 비교는 "수정 전 (빨간색 컵) → 수정 후 (청록색 컵)", 경고는 "컵 색상 불일치" 다.
  곧 **요구는 청록 컵인데 영상에 빨간 컵이 나왔고, 요구는 그대로 둔 채 그 구간만 다시 만들어 맞추는** 흐름이다.
- 지금까지의 수정(`POST /versions/{id}/revisions`)은 요구조건의 값을 바꾸는 것뿐이었다. 값이 이미 같으면
  422 "변경 사항이 없습니다" 로 거절했다. 조건이 같으면 fingerprint 가 같아 부모 클립을 재사용하므로(ADR-0003),
  요구는 맞는데 영상이 틀린 장면을 다시 만들 방법이 없었다. 1차 실호출 5회차에서 실제로 이 상황을 겪었다
  (요구는 청록, 다시 만든 장면의 컵은 빨강).
- '유지할 요소' 는 API·도메인 어디에도 없었다. 프론트 개편 브랜치도 "이 체크 항목은 아직 API 요청에 반영되지 않습니다" 로 표시한다.
- 수정 버전이 '무엇을 하려던 수정인지' 가 남지 않아, 생성기의 지시문·재검수·대시보드가 그것을 쓸 수 없었다.

## 결정

### 1. 수정 의도를 수정 버전에 남긴다 (`RevisionIntent`)
- 종류 `kind`: `CHANGE`(요구조건의 값을 바꾼다) | `REPAIR`(요구조건은 그대로 두고 그 요구조건이 걸린 장면을 다시 만든다).
- 대상(객체·술어·값·범위)과 다시 만드는 장면, CHANGE 의 이전 값, 그대로 둘 요소, 사용자가 쓴 요청 원문.
- 조건(IR)이 아니므로 fingerprint 에 넣지 않는다. `video_versions.intent_json` 에 저장하고
  버전·전후 비교·대시보드 응답의 `revision` 으로 돌려준다. 원본 버전은 null 이다.

### 2. REPAIR: 요구조건을 바꾸지 않고 다시 만든다
- 대상 장면: 범위(SCENES / ALL_APPEARANCES) 안에서 그 요구조건(객체, 술어)이 **그 값으로** 걸려 있는 장면.
  값이 다르면 422 (값을 바꾸는 것은 CHANGE). 짚은 장면에 그 요구조건이 없어도 422.
- 조건이 같으면 부모 클립이 재사용되므로, **대상 장면의 seed 를 다시 지정**한다 (`VideoVersion.scene_seeds`, 지금 값 + 1).
  seed 는 fingerprint 의 일부라 그 장면만 부모와 달라져 새로 만들어지고, 나머지 장면은 그대로 재사용된다.
- `scene_seeds` 는 seed 처럼 수정 버전이 물려받는다. 그래야 다시 만든 장면이 다음 수정 버전에서 재사용된다.
- 값이 같은 CHANGE 는 지금처럼 422 지만, 메시지가 REPAIR 를 안내한다. 계획 미리보기(ADR-0019)는 종류를 스스로 정한다.

### 3. 그대로 둘 요소 (`KeepItem`)
- 객체 요소: `APPEARANCE`(모습) · `ACTION`(동작·자세) · `SHAPE`(형태·크기) — `entity_key` 가 필요하다.
- 장면 요소: `BACKGROUND`(배경) · `LIGHTING`(조명) · `COMPOSITION`(구도·카메라) — `entity_key` 를 비운다.
- 컨셉의 세 체크는 이렇게 옮긴다: 인물·동작 = 인물의 APPEARANCE + ACTION, 배경·조명 = BACKGROUND + LIGHTING, 컵 형태 = 컵의 SHAPE.
- 이 수정이 바꾸는(바로잡는) 것 자체를 그대로 두라는 요청은 422 다. 색을 바꾸면서 그 객체의 APPEARANCE,
  형태를 바꾸면서 SHAPE, 동작을 바꾸면서 ACTION, 조명을 바꾸면서 LIGHTING. 없는 객체는 404.

### 4. 생성기에 수정 지시를 넘긴다 (`SceneGenerationRequest.directive`)
- 수정 버전이 다시 만들기로 한 장면의 요청에 `RevisionDirective` 를 싣는다:
  꼭 지켜져야 할 요구조건(`enforce`: CHANGE 면 바뀐 조건, REPAIR 면 반영되지 않았던 조건), CHANGE 의 이전 값,
  그 장면에 해당하는 유지 요소(그 장면에 나오는 객체의 것과 장면 수준의 것).
- 조건 fingerprint 에는 넣지 않는다. **유지 요소만** 생성 캐시·작업 원장 키(`generation_fingerprint`)에 들어간다.
  유지 요소가 없으면 예전 키 그대로다. 꼭 지켜야 할 요구조건은 이미 조건(또는 seed)에 들어 있다.
- OpenRouter 어댑터
  - 편집 지시문(ADR-0016): REPAIR 는 부모와 조건 차이가 없으므로 지켜지지 않았던 요구조건을 `- cup: color must be teal` 로 적어
    부모 클립을 고친다. 유지 요소가 있으면 `Do not change: barista (appearance, action and pose); cup (shape and size); the background; the lighting.` 을 더한다.
  - 새로 생성하는 프롬프트: 맨 끝에 `Must show: cup color: teal (changed from red).` 또는 `(an earlier render got this wrong).` 을 적는다.
    1차 실호출에서 수정 뒤에도 객체 이름에 예전 표현("빨간 머그컵")이 남아 색이 반영되지 않은 적이 있다.
    유지 요소는 새로 생성하는 프롬프트에 적지 않는다 (부모 영상을 볼 수 없는 모델에게는 뜻이 없다).

## 결과
- 컨셉의 흐름이 API 로 된다: 불일치 확인 → `kind=REPAIR` 수정 → 그 장면만 재생성 → 재검수.
- `POST /revisions` 의 기본값(`kind=CHANGE`, `keep=[]`)은 예전 동작 그대로다. 예전 DB 에는 열 두 개가 기동 때 더해진다.
- 전후 비교에서 REPAIR 로 다시 만든 장면은 `status=REGENERATED`, `conditions_changed=false` 다 (요구조건은 같고 클립만 다르다).
  요구조건의 `before_verdict` / `after_verdict` 로 FAIL → PASS 를 보여 줄 수 있다.
- 한계
  - 한 번의 수정은 요구조건 하나다. "컵 색과 질감을 함께" 는 두 번의 수정이다.
  - 같은 부모에서 같은 장면을 REPAIR 로 두 번 만들면 seed 가 같아 같은 결과가 나온다 (캐시·원장). 또 다른 결과를 원하면
    직전 수정 버전에서 다시 REPAIR 한다 (seed 가 한 번 더 올라간다).
  - 유지 요소를 실제로 지키는 것은 편집 모델에 달렸다. 새로 생성하는 경로에서는 정체성 기준 이미지(ADR-0015)가 인물의 모습만 지킨다.
    지켜졌는지는 유지 요소 검수(ADR-0018)가 판정한다.

## 대안
- REPAIR 를 별도 엔드포인트(`POST /versions/{id}/repairs`)로: 화면의 '해당 구간 재생성' 버튼 하나가 두 경로로 갈라진다.
  같은 본문에 `kind` 를 두는 편이 화면에 단순하다. 계획 미리보기가 종류를 정해 주므로 화면은 그대로 보내면 된다.
- 재생성을 강제하는 표시(장면 키 목록)를 fingerprint 에 넣기: seed 를 바꾸는 것과 효과가 같지만 '다른 시도' 라는 뜻이 seed 에 더 맞고,
  편집을 못 하는 생성기에서도 다른 결과가 나온다.
- 유지 요소를 조건(IR 요구조건)으로 넣기: fingerprint 가 달라져 바뀌지 않은 장면까지 다시 만들게 된다. 기각.
- 유지 요소를 자유 문장으로: 화면의 체크 항목·검수 판정·지시문이 같은 값을 써야 하므로 닫힌 어휘로 둔다.

## 검증
- 단위·계약·API 테스트 (네트워크 없이): `tests/unit/domain/test_revision_intent.py`(유지 요소 검증, REPAIR 대상 장면),
  `tests/unit/application/test_create_revision.py`(REPAIR·seed 재지정·상속), `tests/unit/application/test_revision_pipeline.py`
  (대상 장면만 재생성, 수정 지시 전달, 다음 수정 버전의 재사용, 생성 키), `tests/contract/test_repository_contract.py`(저장소 왕복),
  `tests/api/test_revision_intent_api.py`, `tests/unit/generator/test_openrouter_edit.py`·`test_openrouter_generator.py`(지시문).
- 실호출 (2026-11-12, 연구 문서 §5)
  - 가짜 위쪽 + 실제 LLM·VLM: 요구는 청록인데 빨간 컵 클립이 나온 버전 → REPAIR → 두 장면만 다시 만들고 요구 반영률 11/13 → 13/13.
  - 실제 FLUX Video Edit: 1차 5회차에서 실제로 생긴 불일치 버전을 REPAIR + 유지 요소 5개로 편집. 같은 인물·자세·배경·구도에서
    컵만 청록으로 바뀌었고(눈으로 확인), 컵 색 요구조건 2건 PASS, 유지 요소 8건 PASS. 지시문은
    `- cup: color must be teal` + `Do not change: ...` 였다.
  - CHANGE + 글에서 읽은 유지 요소(인물의 모습·동작, 배경)로 실제 FLUX 편집 (컨셉 예시 4장면, 4회차):
    지시문 `- red_mug: color changes from red to teal` + `Do not change: barista (appearance, action and pose); the background.`
    같은 자세·구도·배경에서 컵만 청록, 요구 반영 18/18, 유지 요소 4건 PASS.
  - 편집하지 못해 새로 생성된 경우 (3·5회차): `Must show: cup color: teal (changed from red).` 로 색은 두 장면 모두 반영됐다.
    그러나 자세·구도·컵 모양이 달라졌고, 유지 요소 8건 가운데 4건이 FAIL 이었다. 한계에 적은 대로다:
    새로 생성하는 경로는 유지 요소를 지키지 못한다.
