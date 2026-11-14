# ADR 목록

설계 결정 기록(Architecture Decision Record)의 색인이다. ADR 을 더하면 이 표에 한 줄을 더한다
(`tests/architecture/test_docs.py` 가 빠진 줄과 번호 중복을 잡는다).

- 번호는 네 자리이고 겹치지 않는다. 여러 브랜치가 동시에 쓸 때는 번호를 먼저 나눠 갖는다.
- 파일 이름은 `NNNN-짧은-영문-제목.md`, 첫 줄은 `# ADR-NNNN 제목` 이다.
- 뒤의 ADR 이 앞의 결정을 바꾸면 앞의 ADR 에 "보완 (날짜): ADR-NNNN" 한 줄을 더한다.

| 번호 | 제목 | 상태 |
|---|---|---|
| [0001](0001-layered-hexagonal.md) | 4계층 + Ports & Adapters | 채택 (2026-11-07) |
| [0002](0002-model-agnostic-ir.md) | 모델 비의존 중간표현(IR) | 채택 (2026-11-07) |
| [0003](0003-scene-level-selective-regeneration.md) | 장면 단위 생성과 fingerprint 기반 선택적 재생성 | 채택 (2026-11-07) |
| [0004](0004-persistence-dual-write.md) | IR 문서 + ERD 정규화 행 이중 저장 | 채택 (2026-11-07) |
| [0005](0005-job-dispatcher.md) | 비동기 작업은 디스패처 포트로 추상화 | 채택 (2026-11-07) |
| [0006](0006-error-model.md) | 실행 오류와 검수 판정의 분리 | 채택 (2026-11-07) |
| [0007](0007-predicate-vocabulary-and-relations.md) | 술어 어휘, 관계 조건 규칙, 장면 타임라인 검증 | 채택 (2026-11-07, 2차 스키마 검토 반영) |
| [0008](0008-value-normalization-and-relation-kinds.md) | 값 정규화, 객체 종류 ANIMAL, 관계의 배경 객체 금지 | 채택 (2026-11-07, OpenAI 실호출 결과 반영) |
| [0009](0009-frontend-integration-contract.md) | 프론트 연동 계약 | 채택 (2026-11-09, feature-frontend-mvp 연동 검토 반영) |
| [0010](0010-demo-runtime-key-and-launcher.md) | 시연용 화면 키 입력과 OS 공통 실행 런처 | 채택 (2026-11-09, 새 기기 시연 준비 반영) |
| [0011](0011-openrouter-video-generator.md) | OpenRouter 영상 생성 어댑터와 재과금 방지 규칙 | 채택 (2026-11-11) |
| [0012](0012-generation-timeout-partial-results-progress.md) | 생성 시간 제한, 실패 시 부분 결과 보존, 단계 진행 관찰 | 채택 (2026-11-12) |
| [0013](0013-restart-recovery-and-persistent-generation-cache.md) | 재시작 복구와 생성 결과 캐시 영속화 (외부 작업 원장) | 채택 (2026-11-12) |
| [0014](0014-comparison-and-dashboard-api.md) | 전후 비교·대시보드 집계 API | 채택 (2026-11-12, 이슈 #13) |
| [0015](0015-identity-references-and-consistency-check.md) | 장면 사이 객체 일관성: 정체성 기준 이미지와 실제 일관성 검수기 | 채택 (2026-11-12) |
| [0016](0016-parent-clip-editing.md) | 수정 버전의 부모 클립 편집 포트와 실제 보존 검수기 | 채택 (2026-11-12) |
| [0017](0017-revision-intent-repair-and-keep.md) | 수정 의도: 다시 맞추기(REPAIR)와 그대로 둘 요소 | 채택 (2026-11-12) |
| [0018](0018-requirement-and-keep-checkers.md) | 실제 요구 반영 검수기와 유지 요소 검수기 | 채택 (2026-11-12) |
| [0019](0019-revision-plan-and-change-request.md) | 변경 계획 미리보기와 글로 쓴 변경 요청의 해석 | 채택 (2026-11-12) |
| [0020](0020-production-preview-and-held-parent-url.md) | 변경 계획이 '어떻게 만들어질지' 를 알려 준다: 생성기 미리보기와 부모 주소 보관 | 채택 (2026-11-12) |
| [0021](0021-provisional-results.md) | 일시적인 사정으로 낸 결과는 요청의 키에 남기지 않는다 (임시 결과) | 채택 (2026-11-12) |
| [0022](0022-edit-evaluation-cli.md) | 지표 1 평가 CLI: 인프로세스 실행과 측정 기준별 비대상 변경률 | 채택 (2026-11-14, 이슈 #21) |
| [0023](0023-direct-ir-input-draft-edit-and-display-name.md) | IR 직접 입력·DRAFT IR 수정과 객체의 표시 이름 | 채택 (2026-11-14, 이슈 #10·#11) |
| [0024](0024-reference-image-upload.md) | 참조 이미지 업로드 | 채택 (2026-11-14, 이슈 #12) |
| [0025](0025-theme-representation-and-extraction.md) | 테마: 편 사이의 규칙을 IR 과 따로 두고, 영상 한 편에서 뽑는다 | 채택 (2026-11-14) |
| [0026](0026-series-episodes-identity-and-recorded-narration.md) | 테마로 편 만들기: 고정 요소는 코드가 넣고, 편 사이 정체성은 기준 이미지로 잇고, 내레이션은 사람이 녹음한다 | 채택 (2026-11-14) |
| [0027](0027-post-production-layer-and-local-correction.md) | 화면 문구는 후반 합성 층에 두고, 어긋난 화면 정보는 종류마다 가장 좁은 방법으로 고친다 | 채택 (2026-11-14) |
| [0028](0028-script-extraction-and-evidence.md) | 대본: 영상의 소리에서 줄 단위로 뽑고, 구조화의 근거로 쓴다 | 채택 (2026-11-14) |
| [0029](0029-script-only-correction.md) | 대본만 고친다: 뽑은 대본의 줄 교정과 편의 녹음 대본 수정 | 채택 (2026-11-14) |
| [0030](0030-ci-quality-gates-and-baselines.md) | CI 품질 게이트와 기준선 방식 | 채택 (2026-11-14) |
| [0031](0031-concept-placement-glossary-compat-paths.md) | 개념 단위 배치 규칙 · 용어집 · 옛 경로의 호환 모듈 | 채택 (2026-11-14) |
| [0032](0032-no-machine-paths-single-source-settings.md) | 컴퓨터에 묶인 경로와 값의 사본을 두지 않는다: 절대 경로 금지 · 설정값은 출처 하나 · 이름 없는 수 금지 | 채택 (2026-11-15) |
| [0033](0033-fastapi-starlette-upgrade-for-known-vulnerabilities.md) | FastAPI · Starlette 의 판을 올린다: 알려진 취약점, 그리고 Starlette 를 직접 고정한다 | 채택 (2026-11-15) |

나눠 가진 번호는 지금 없다 (0022~0029 를 나눠 가졌던 브랜치들이 모두 dev 에 들어왔다). 다음 ADR 은 0034 다.
