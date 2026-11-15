# ADR-0014 전후 비교·대시보드 집계 API

- 상태: 채택 (2026-11-12, 이슈 #13)

## 맥락
- US-04 AC-41 은 "요구사항 목록, 반영 상태, 수정 전/후 결과가 한 화면에서 통합 제공됨", AC-43 은 "의도한 수정 영역만 반영되고 기타 영역은
  유지된 결과를 두 화면으로 비교 시각화함" 이다.
- 지금 화면이 이를 그리려면 두 버전을 각각 `GET /versions/{id}` 로 받아 장면별 `clip_uri` 를 직접 대조하고, `GET /checks` 를 두 번 더 불러
  지표를 빼야 한다 (2026-11-11 실제 실행 검증도 이렇게 했다). 재사용 여부·바뀐 요구조건·검수 요약 차이를 한 번에 주는 API 가 없었다.
- 대시보드는 프로젝트의 버전 트리와 버전별 상태·지표가 필요한데 `GET /projects/{id}` 는 버전 ID·상태·부모·생성 시각만 준다.

## 결정
- **읽을 때 계산한다**: 저장 모델·포트를 바꾸지 않는다. 기존 저장소 메서드(`versions.get`·`list_by_project`, `checks.list_by_version`)로 읽고
  도메인 함수(`domain/services/comparison.py` — 2026-11-15 부터 `domain/comparison.py`)로 계산한다. 대시보드의 "바뀐 장면 수" 도 같은 함수를 쓴다.
- **`GET /api/versions/{version_id}/comparison?against={version_id}`** (`against` 를 생략하면 부모와 비교)
  - `before`(기준)·`after`(대상) 버전 요약: 상태·오류·생성 시각·`video_url`·`video_media_type`·검수 요약(`checks`).
  - `checks_delta`: 반영률·일관성·보존율의 after − before. 한쪽이라도 null 이면 null.
  - `summary`: 장면 상태별 개수(모든 상태를 키로), 조건이 바뀐 장면 목록, 바뀐 요구조건 수.
  - `scenes[]`: 장면별 `status`·`conditions_changed`, 양쪽 장면(`before`/`after`: 시간 구간·설명·`clip_url`·`clip_media_type`,
    그 버전에 장면이 없으면 null), 그 장면의 요구조건 `requirements[]`.
- **장면 상태**: 양쪽이 같은 클립 산출물(URI)이면 `REUSED`, 양쪽 클립이 다르면 `REGENERATED`, 한쪽 IR 에만 있으면 `ADDED`/`REMOVED`,
  양쪽에 있지만 한쪽이라도 클립이 없으면(생성 전·처리 중·실패) `UNAVAILABLE` (영상 조회의 `MEDIA_NOT_AVAILABLE` 과 같은 뜻).
  - 재사용을 fingerprint 가 아니라 URI 로 판정한다. 생성 단계는 부모 클립의 fingerprint 가 같을 때 그 클립을 그대로 쓰므로 부모와 비교하면 둘이 같다.
    부모가 아닌 버전(형제·조상)과 비교할 때 조건만 같고 따로 생성한 클립은 실제 모델에서 바이트가 다를 수 있어, 이를 재사용이라 하면 화면이 다른 영상을
    같은 것으로 보여 준다. 조건이 같은지는 `conditions_changed` 로 따로 준다.
  - `conditions_changed`: `IR.scene_fingerprint`(장면 조건 + 등장 객체 정의) 비교. 클립이 없어도 계산되므로 DRAFT 수정본에서 "재생성될 장면" 을
    보여 줄 수 있다. 추가·삭제된 장면은 참.
- **요구조건 비교**: 장면의 모든 요구조건을 준다 (바뀌지 않은 것 포함 — AC-41 의 요구사항 목록). 각 행은 `change`(UNCHANGED / CHANGED /
  ADDED / REMOVED), 양쪽 키·값, 양쪽 REQUIREMENT 판정(`before_verdict`/`after_verdict`, 검수 결과의 `requirement_key` 로 잇는다),
  `value_kind`, `relational`.
  - 짝짓기는 요구조건 키로 한다. 값만 바뀐 속성 조건은 키가 같아 `CHANGED` 다. 관계 상대가 바뀌면 키가 달라지므로
    (`intro:barista:holds:cup` → `intro:barista:holds:phone`), 같은 (객체, 술어)의 조건이 양쪽에 하나씩 남을 때 `CHANGED` 로 묶는다.
  - 장면 수준 조건(`entity_key` null: 장소·조명·분위기·카메라)도 같은 규칙이다.
  - 순서: 대상 버전의 장면·요구조건 순서, 기준에만 있는 것은 뒤에 붙인다.
- **오류** (`{error_code, error_message}`, ADR-0009): 없는 버전·기준 버전 404 `NOT_FOUND`. 부모 없는 원본에서 `against` 생략, 다른 프로젝트의
  기준 버전은 422 `VALIDATION_ERROR`. 빈 `against=` 는 요청 검증 422(`details`). IR 이 없는 버전(구조화 중·구조화 실패)은 409
  `VERSION_NOT_COMPARABLE` — `VersionNotRevisable` 처럼 `InvalidStateTransition` 하위라 구조화가 끝나면 다시 부를 수 있다.
- **`GET /api/projects/{project_id}/dashboard`**
  - `versions[]`(생성 순): 버전 요약 + `depth`(원본 0)·`child_version_ids`·`scene_count`·`changed_scene_count`
    (부모 대비 `conditions_changed` 장면 수, 원본이거나 IR 이 없으면 null). 트리는 `parent_version_id`/`child_version_ids` 로 그린다.
  - `summary`: `version_count`, `state_counts`(모든 `VersionState` 를 키로, 0 포함), `latest_version_id`(가장 늦게 만든 버전),
    `latest_completed_version_id`.
  - "바뀐 장면 수" 는 클립이 아니라 조건 기준이라 생성 전·실패 버전에도 값이 있다. 같은 프로젝트의 수정본은 seed·참조 이미지를 물려받으므로
    완료된 수정본에서는 재생성된 장면 수와 같다.
- **표기**: 새 값은 기존 Enum 처럼 대문자다. 응답 필드는 기존 `state`·`verdict` 처럼 `str` 로 두고 허용값은 OpenAPI 설명에 적는다.
  내부 URI(`*_uri`)는 새 응답에 넣지 않는다 (재생은 `*_url`, 재사용 판정은 `status`).

## 결과
- 화면은 비교 뷰어를 `GET /comparison` 한 번, 대시보드를 `GET /dashboard` 한 번으로 그린다. 요구조건별 판정이 함께 오므로
  "의도한 수정이 반영됐는가" 를 `GET /checks` 없이 보여 줄 수 있다.
- 대시보드는 버전마다 검수 결과를 한 번씩 읽는다 (버전 수만큼 쿼리). 시연 규모(프로젝트당 수십 버전)에서는 문제없고, 커지면 저장소에 집계 쿼리를 더한다.
- 장면별 보존 판정(PRESERVATION 의 장면 점수)은 비교 응답에 넣지 않았다. 지금은 `GET /checks` 의 `scene_key` 로 잇고, 화면 요구가 생기면 더한다.
- 남은 일: 프론트 비교 뷰어·대시보드 화면(이슈 #14), 버전이 많을 때의 집계 쿼리.

## 검증 (2026-11-12, Fake 어댑터)
- 도메인: `tests/unit/domain/test_comparison.py` — 값·장면 수준·관계 상대 변경, 추가·삭제, URI 재사용 판정, 조건만 같은 클립, 클립 없음,
  장면 추가·삭제, 객체 정의 변경, 지표 차이.
- 유스케이스: `tests/unit/application/test_comparison_queries.py` — 부모 기본값, 형제 비교, 판정 연결, DRAFT·FAILED 수정본, 오류, 대시보드 트리·집계.
- API: `tests/api/test_comparison_dashboard_api.py` — 응답 형태, 같은 바이트의 재사용 클립, 오류 형식, OpenAPI 계약 서명.
- 인수: `tests/acceptance/test_us04_dashboard.py` — AC-41(대시보드 + 비교 두 번으로 트리·요구사항 목록·판정·전후 결과),
  AC-43(컵 색 수정: 컵 장면만 CHANGED·PASS, 나머지 REUSED·같은 바이트·보존율 ≥ 0.9 / 배경 조명을 한 장면만 수정).
