# 모듈 지도 — 어디에 무엇이 있나

뒤에 들어오는 사람이 "이 일은 어디에 있는가" 를 찾는 문서다. 개념에서 시작해 패키지와 포트·주요 클래스로 내려간다.
배치 규칙은 [ADR-0031](adr/0031-concept-placement-glossary-compat-paths.md)(개념 단위 배치), 낱말은 [GLOSSARY.md](GLOSSARY.md) 를 따른다.
계층과 의존 방향은 [ARCHITECTURE.md](ARCHITECTURE.md) §3 에 있다.

이 문서는 코드와 함께 고친다. 패키지를 더하거나 옮기면 §2 의 표를 고쳐야 테스트가 통과한다
(`tests/architecture/test_docs.py`).

## 1. 개념에서 찾기

개념 하나는 계층을 따라 도메인 → 포트·유스케이스 → 어댑터에 걸쳐 있다. 경로는 `src/frame/` 아래다.

| 개념 | 도메인 (`domain/`) | 포트 · 유스케이스 · 파이프라인 (`application/`) | 어댑터 · 그 밖 |
|---|---|---|---|
| 구조화 (요구사항 → IR) | `ir.py` `IntermediateRepresentation`, `vocabulary.py` `PredicateVocabulary` | `ports/structurer.py` `RequirementStructurer` · `use_cases/create_project.py`, `structure_requirements.py` | `adapters/structurer/` |
| 버전과 상태 | `models.py` `VideoVersion`, `state_machine.py`, `progress.py` | `use_cases/accept_generation.py`, `queries.py` | `adapters/persistence/` |
| 생성 (장면 클립 만들기) | `artifacts.py` `GeneratedClip` | `ports/generator.py` `VideoGenerator` · `pipeline/stages.py` `GenerateStage`, `pipeline/requests.py` | `adapters/generator/` |
| 최종 영상 조립 | `artifacts.py` `VideoArtifact` | `ports/generator.py` `VideoAssembler` | `adapters/generator/ffmpeg.py` `FfmpegVideoAssembler` |
| 부모 클립 편집 | — | `ports/generator.py` `ParentClip`·`RevisionDirective` | `adapters/generator/openrouter.py` |
| 재사용 판단 (해시·키) | `ir.py` 의 fingerprint, `hashing.py` | `ports/generator.py` `generation_fingerprint`·`result_key` | `adapters/generator/cache.py`, `ledger.py` |
| 정체성 기준 이미지 | `services/identity.py`, `models.py` `ReferenceImage` | `ports/identity.py` `IdentityReferenceExtractor` · `pipeline/stages.py` | `adapters/identity/` |
| 검수 (요구 반영 · 유지 요소 · 일관성 · 보존) | `checks.py` `CheckResult`, `metrics.py`, `services/comparison.py` | `ports/checker.py` `Checker`, `checks.py` `CheckSuite` · `pipeline/stages.py` `CheckStage` | `adapters/checker/` |
| 수정 의도 · 변경 계획 | `revision.py` `RevisionIntent`, `services/propagation.py`, `services/revision_planning.py` | `ports/change_request.py` · `use_cases/create_revision.py`, `plan_revision.py` | `adapters/change_request/` |
| 추적 | `tracking.py` | `ports/tracker.py` `ObjectTracker` · `pipeline/stages.py` `TrackStage` | `adapters/tracker/` |
| 영상 처리 (ffmpeg) | — | — | `adapters/shared/media/` — 찾기·실행 `ffmpeg.py`, 프레임 뽑기 `frames.py`, 크기 `probe.py`, 유사도(SSIM) `similarity.py`. 클립 정규화·검사는 `adapters/generator/ffmpeg.py` |
| LLM·VLM 과 API 키 | — | `ports/llm.py` `LLMClient`·`LLMKeySource` | `adapters/llm/`, 응답을 물어본 것에 맞추기 `adapters/shared/vlm/answers.py`, 키 파일 읽기·키 표시 `adapters/shared/api_keys.py` |
| 저장 (산출물 · DB) | `artifacts.py` | `ports/artifact_store.py`, `ports/repositories.py`, `ports/unit_of_work.py` | `adapters/storage/`, `adapters/persistence/` |
| 작업 실행 · 복구 | `state_machine.py` | `ports/jobs.py` `JobDispatcher` · `use_cases/run_generation_pipeline.py`, `recover_pending_versions.py` · `pipeline/` | `adapters/jobs/`, `presentation/recovery.py` |
| HTTP API | — | `dto.py`, `use_cases/queries.py` | `presentation/` |
| 조립 (설정 → 어댑터) | — | — | `config/` — 설정 `settings.py`, 키 → 어댑터 `registrations/<포트>.py`, 엮기 `container.py` |
| 성공 지표 평가 | `metrics.py` | — | `evaluation/` |
| 실행 도구 (시연 런처 · 번들) | — | — | `tools/`, `scripts/` |

## 2. 패키지 목록

패키지마다 맡는 개념과 밖에서 쓰는 이름이다. 안쪽 구현은 각 패키지의 `__init__.py` 설명을 본다.

| 패키지 | 맡는 개념 | 밖에서 쓰는 이름 |
|---|---|---|
| `src/frame/` | 패키지 최상위 | `__version__` |
| `src/frame/domain/` | 업무 규칙. 표준 라이브러리만 쓴다 | `IntermediateRepresentation`, `VideoVersion`, `RevisionIntent`, `CheckResult`, 열거형(`enums.py`), 오류(`errors.py`) |
| `src/frame/domain/services/` | 여러 엔티티에 걸친 규칙 | 수정 전파 `propagation`, 수정 요청 풀기 `revision_planning`, 정체성 기준 `identity`, 버전 비교 `comparison` |
| `src/frame/application/` | 유스케이스와 포트 | `CheckSuite`, `dto.py` 의 명령·결과, 어댑터 오류 계층 `errors.py` |
| `src/frame/application/ports/` | 바깥에 요구하는 계약 | `RequirementStructurer`, `VideoGenerator`, `VideoAssembler`, `IdentityReferenceExtractor`, `ObjectTracker`, `Checker`, `ChangeRequestInterpreter`, `LLMClient`, `ArtifactStore`, 저장소·`UnitOfWork`, `JobDispatcher` |
| `src/frame/application/use_cases/` | 유스케이스 하나가 모듈 하나 | `CreateProject`, `StructureRequirements`, `AcceptGeneration`, `RunGenerationPipeline`, `CreateRevision`, `PlanRevision`, `ProjectQueries`, `RecoverPendingVersions` |
| `src/frame/application/pipeline/` | 생성 → 추적 → 검수 단계와 실행기 | `PipelineRunner`, `GenerateStage`, `TrackStage`, `CheckStage`, `PipelineContext` |
| `src/frame/adapters/` | 포트의 구현. 최상위 모듈 넷(`common.py`, `http_transport.py`, `video_tools.py`, `vlm_answers.py`)은 옛 경로의 호환 모듈이다 — 구현은 `shared/` 에 있고 새 코드는 쓰지 않는다 (ADR-0031) | — |
| `src/frame/adapters/shared/` | 어댑터 패키지들이 함께 쓰는 것 (어댑터끼리는 서로 import 하지 않는다) | `api_keys` (`find_key`, `mask_key`), `http` (`HttpTransport`, `UrllibTransport`, `HttpResponse`, `TransportError`), `runtime` (ID 생성기 `UuidIdGenerator`·`SequentialIdGenerator`, 시계 `SystemClock`·`FixedClock`) |
| `src/frame/adapters/shared/media/` | 영상 처리 도구 (ffmpeg) | `ffmpeg` (`resolve_ffmpeg`, `require_ffmpeg`, `run_ffmpeg`, `run_reading`, `tail`, `FfmpegNotRun`, `VideoToolError`), `frames` (`extract_frame`, `Box`, `Image`), `probe` (`video_size`), `similarity` (`ssim`) |
| `src/frame/adapters/shared/vlm/` | 이미지를 읽는 모델(VLM)의 답 읽기 | `answers` (`match_answers`, `requested_key`) |
| `src/frame/adapters/structurer/` | 구조화 | `RuleBasedStructurer`, `LLMStructurer`, `FakeStructurer` |
| `src/frame/adapters/generator/` | 장면 클립 생성, 클립 정규화, 최종 영상 조립, 생성 캐시·작업 원장 | `OpenRouterVideoGenerator`, `FakeVideoGenerator`, `CachingVideoGenerator`, `RetryingVideoGenerator`, `TimeoutVideoGenerator`, `DelegatingVideoGenerator` (데코레이터의 바탕), `FfmpegVideoAssembler`, `FakeVideoAssembler` |
| `src/frame/adapters/identity/` | 정체성 기준 이미지 뽑기 | `VlmCropIdentityExtractor`, `FrameIdentityExtractor`, `FakeIdentityExtractor` |
| `src/frame/adapters/checker/` | 검수 | `VlmRequirementChecker`, `VlmConsistencyChecker`, `VlmPreservationChecker`, `SsimPreservationChecker`, 가짜 검수기 셋 |
| `src/frame/adapters/change_request/` | 글로 쓴 변경 요청의 해석 | `LLMChangeRequestInterpreter`, `RuleBasedChangeRequestInterpreter` |
| `src/frame/adapters/llm/` | LLM·VLM 클라이언트와 키 저장소 | `OpenAIJsonClient`, `ReplayLLMClient`, `LLMKeyStore` |
| `src/frame/adapters/tracker/` | 객체 추적 | `FakeObjectTracker`, `OpenCvObjectTracker` (뼈대) |
| `src/frame/adapters/storage/` | 산출물 저장소 | `LocalFileArtifactStore`, `InMemoryArtifactStore` |
| `src/frame/adapters/persistence/` | 저장소와 저장 단위 — 메모리 구현 | `InMemoryUnitOfWork`, `in_memory_uow_factory` |
| `src/frame/adapters/persistence/sqlalchemy/` | 저장소와 저장 단위 — SQLAlchemy 구현 | `SqlAlchemyUnitOfWork`, `sqlalchemy_uow_factory`, `create_engine_from_url` |
| `src/frame/adapters/jobs/` | 작업 디스패처 | `InlineJobDispatcher`, `ThreadPoolJobDispatcher`, `RecordingJobDispatcher` |
| `src/frame/config/` | 조립 지점 | `Settings`, `AdapterRegistry`, `default_registry`, `Container`, `build_container` |
| `src/frame/config/registrations/` | 기본 등록 — 설정 키가 어느 어댑터가 되는지 (모듈 하나가 포트 하나) | `register_defaults`, 포트별 `register` (`llm`, `structurer`, `change_request`, `generator`, `assembler`, `identity`, `tracker`, `checker`, `storage`, `persistence`, `jobs`) |
| `src/frame/presentation/` | HTTP API | `create_app`, 스키마 `schemas.py`, 응답 변환 `mappers.py`, 오류 응답 `errors.py` |
| `src/frame/presentation/routers/` | 자원별 라우터 | `projects`, `versions`, `settings`, `vocabulary` |
| `src/frame/evaluation/` | 성공 지표 평가 | `evaluate_case`, `evaluate_cases` |
| `src/frame/tools/` | 명령줄 도구 | `demo` (시연 런처), `bundle` (번들 빌드), `export_openapi`, `structure_cli` |
