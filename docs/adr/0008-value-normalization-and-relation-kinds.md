# ADR-0008 값 정규화, 객체 종류 ANIMAL, 관계의 배경 객체 금지

- 상태: 채택 (2026-11-07, OpenAI 실호출 결과 반영)

## 맥락
gpt-4.1-mini 로 브리프 3종을 구조화해 보니 스키마는 통과했지만 어휘 수준의 한계가 드러났다.
1. 강아지가 `PERSON` 으로 분류됐다 (동물 종류가 없었다).
2. `dog.near = park` 처럼 배경을 관계 상대로 썼다. 배경은 "장소" 이지 관계의 대상이 아니다.
3. 같은 뜻의 값이 `white_shirt` / `white shirt` / `White Shirt` 로 흩어져 수정 전파의 `current_value` 매칭과 검수 비교가 어긋난다.
4. "던진다" 같은 동작이 `holds` 관계로 표현됐다.

## 결정
- `EntityKind.ANIMAL` 추가. 보정 단계는 `animal`/`pet`/`place`/`item` 같은 모델 출력 별칭을 정식 종류로 맞춘다.
- 관계 조건의 주체·상대는 `BACKGROUND` 일 수 없다 (`IR.validate`, 수정 전파, 보정 단계 모두에서 강제).
  - 보완 (2026-11-14, 감사 A-20): 수정 전파는 상대 객체만 보고 주체는 보지 않아, 배경 객체를 관계의 주체로 만드는 수정이 통과했다.
    수정 요청을 풀 때(`resolve_revision`) 전파한 중간표현을 `IR.validate` 로 다시 본다 — 규칙은 `validate` 한 곳에 두고,
    구조화에서 거부되는 것은 수정으로도 만들 수 없다. 수정 전부터 규칙에 어긋나 있던 중간표현은 막지 않는다 (그 수정이 만든 문제가 아니다).
  장소는 장면 수준 `location` 조건으로 표현한다. 보정 단계는 배경·미정의 객체를 향한 관계를 제거하고 메모를 남긴다.
- 값 정규화를 값 종류별로 정한다 (`PredicateVocabulary.normalize_value`):
  COLOR/TEXT 는 소문자·밑줄→공백·공백 정리, LITERAL(`text`, `brand`)은 철자 보존, NUMBER 는 숫자 정규화,
  POSITION 은 top/bottom/left/right/center/foreground/background 토큰(한국어 별칭 포함), ENTITY 는 객체 키 그대로.
  `RequirementSpec` 과 `AttributeChange` 가 생성 시점에 적용한다.
- 어휘 확장: `brand`, `hair`, `age`, `gender`, `pose`, `style`, `location`, `time_of_day`, `weather`.
  장면 수준 술어(location, lighting, mood, camera, time_of_day, weather)를 프롬프트에서 별도로 안내한다.
- 프롬프트(PROMPT_VERSION 2026-11-07.2)에 종류·관계·동작·값 형식 규칙과 확장된 예시를 넣는다.
- `schema_version` 은 1 을 유지한다 (종류 추가는 읽기 호환, 값 정규화는 저장 형식 변화가 아님).

## 결과
- 같은 브리프를 다시 구조화해도 값이 한 형태로 수렴해 수정 전파와 검수 비교가 안정된다.
- 배경과의 관계가 사라지고 장소가 장면 조건으로 모여 US-03 장면 간 일관성 검수 대상이 명확해진다.
- 남은 과제: 술어별 값 형식 검증(HEX 색상 등), 동작 어휘 표준화, 골드셋으로 분류 정확도 측정.
