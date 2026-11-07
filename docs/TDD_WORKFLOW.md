# TDD 워크플로 가이드

이 프로젝트의 모든 기능은 **테스트 리스트 → Red → Green → Refactor** 순서로 만든다.
AC(Acceptance Criteria) 번호는 테스트 마커로 남겨 Definition of Done 과 바로 대조한다.

## 1. 작성 순서: 안쪽에서 바깥쪽으로

| 순서 | 계층 | 테스트 위치 | 규칙 |
|---|---|---|---|
| 1 | Domain | `tests/unit/domain` | 외부 의존 0. 값 객체·상태 규칙·지표 식을 먼저 고정한다 |
| 2 | Application | `tests/unit/application` | Fake 어댑터 + 메모리 UoW + Recording 디스패처. "접수 직후 QUEUED" 와 "실행 후 COMPLETED" 를 각각 단언 |
| 3 | Adapter 계약 | `tests/contract` | 포트별 공용 스위트. 새 구현은 `params` 한 줄 추가 (실제 모델은 `slow`/`gpu`) |
| 4 | Integration | `tests/integration` | SQLite in-memory 로 SQL 매퍼·FK·조건부 갱신 |
| 5 | API / Acceptance | `tests/api`, `tests/acceptance` | TestClient 로 Endpoint Catalog, AC 시나리오 |

## 2. 테스트 더블 정책

| 더블 | 구현 | 용도 |
|---|---|---|
| Fake (1급 어댑터) | `adapters/*/fake.py` | 결정적 결과. 로컬 데모에도 그대로 쓴다 |
| 실패 주입 (Stub) | `FakeVideoGenerator(fail_scene_keys=…, error=…)`, `FakeObjectTracker(error=…)`, `FakeStructurer(error=…)` | FAILED 경로·원본 보존 검증 |
| 기록 (Spy) | `FakeVideoGenerator.requests`, `FakeStructurer.calls`, `RecordingJobDispatcher.dispatched` | "바뀐 장면만 재생성됐는가" 같은 상호작용 단언 |
| 결정적 인프라 | `SequentialIdGenerator`, `FixedClock`, `InMemoryArtifactStore` | ID·시각·URI 를 예측 가능하게 |
| 컨테이너 주입 | `build_container(settings, overrides={"generator": …})` | 설정 키 없이 특정 더블을 꽂는다 |

Mock 라이브러리는 쓰지 않는다. 포트가 작고 Fake 가 있으므로 `unittest.mock` 보다 읽기 쉽고 깨지기 어렵다.

## 3. 사이클 예시 ① — US-01 AC-02 "연관 장면 자동 전파"

1. **테스트 리스트**: 모든 등장 장면에 반영 / 지정 장면만 반영 / 객체 없는 장면 거부 / 원본 IR 불변 / 같은 값이면 변경 없음
2. **Red**: `tests/unit/domain/test_propagation.py` 에 `test_change_propagates_to_all_scenes_where_entity_appears` 작성 → `DefaultPropagationPolicy` 가 없어 실패
3. **Green**: `domain/services/propagation.py` 에 최소 구현 (대상 장면 결정 + 요구조건 교체/추가)
4. **Refactor**: 대상 장면 결정을 `_resolve_targets` 로 분리, 오류 메시지 정리. 테스트는 그대로 초록
5. **바깥쪽으로**: `test_create_revision.py` (유스케이스) → `test_api_flow.py` 의 `POST /revisions` → `test_us01_selective_edit.py` 의 `@pytest.mark.ac("AC-02")`

## 4. 사이클 예시 ② — 실제 검수기 추가 (예: 색상 히스토그램 일관성)

1. `tests/contract` 에 `Checker` 계약 테스트를 만든다: 추적 실패 구간은 `UNDETERMINED`, 점수는 0~1, `warnings` 는 장면 구간 안.
   `params` 에 `FakeConsistencyChecker` 와 새 `ColorHistogramConsistencyChecker` 를 나란히 둔다 → 새 구현은 빨강
2. `adapters/checker/color_histogram.py` 구현 (OpenCV 는 어댑터 안에서만 import)
3. `config/registry.py` 에 `register(Checker, "color_consistency", …)` 추가, `.env` 의 `FRAME_CHECKERS` 에 키 추가
4. `tests/acceptance/test_us03_tracking.py` 를 실제 클립 fixture 로 `slow` 마커 아래 한 번 더 돌린다

## 5. 새 어댑터 체크리스트

- [ ] 포트의 docstring 에 적힌 계약(반환 형태·예외·결정성)을 계약 테스트로 먼저 적었다
- [ ] 실패는 `AdapterError` 하위 예외로만 던진다 (`GenerationError`, `TrackingError`, …)
- [ ] 판정 불가는 예외가 아니라 `UNDETERMINED` 로 돌려준다
- [ ] 모델별 프롬프트·파라미터 변환은 어댑터 안에 있다 (IR 은 바뀌지 않는다)
- [ ] `default_registry()` 에 키를 등록했고, `/health` 에 키가 보인다
- [ ] 느린 테스트는 `@pytest.mark.slow` / `@pytest.mark.gpu`

## 6. 명령어

```bash
.venv/Scripts/python -m pytest                         # 전체 (약 1초, Fake + SQLite)
.venv/Scripts/python -m pytest -m "not slow and not gpu"   # CI 기본
.venv/Scripts/python -m pytest -m ac --co -q           # AC 매핑된 테스트 목록 (DoD 대조용)
.venv/Scripts/python -m pytest --cov=frame --cov-report=term-missing
.venv/Scripts/python -m ruff check src tests
```

## 7. 지표 측정 절차 (T12)

1. Fake 로 하네스 동작을 검증한다: `tests/unit/test_edit_metrics.py`, `tests/acceptance/test_us01_selective_edit.py`
2. 실제 생성·검수 어댑터로 컨테이너를 조립한 뒤 단일 속성 수정 20건을 `CreateRevision → AcceptGeneration` 으로 실행한다
3. 각 수정 버전의 `GET /checks` 결과를 `EditCase` 로 묶어 `evaluate_cases()` 에 넣는다
4. `success_rate ≥ 0.8`, `mean_non_target_change_rate ≤ 0.1` 이면 `meets_target` 이 참이다. 미판정(`UNDETERMINED`)은 성공으로 세지 않는다
5. 결과(JSON)와 판정 근거(클립 URI·검수 detail)를 저장소에 기록한다 (Sprint Backlog 평가 계획)
