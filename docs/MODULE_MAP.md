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
| 구조화 (요구사항 → IR) | `ir.py` `IntermediateRepresentation`, `vocabulary.py` `PredicateVocabulary`, 버전에 담기 전의 규칙 `structuring_rules.py` | `ports/structurer.py` `RequirementStructurer` · `use_cases/create_project.py` (글 · IR 직접 입력), `structure_requirements.py`, `edit_draft_ir.py` (DRAFT 의 IR 수정) | `adapters/structurer/` |
| 참조 이미지 업로드 | `models.py` `ReferenceImage` | `use_cases/upload_reference_image.py` `UploadReferenceImage` | `presentation/routers/reference_images.py` |
| 버전과 상태 | `models.py` `VideoVersion`, `state_machine.py`, `progress.py` | `use_cases/accept_generation.py`, `queries.py` | `adapters/persistence/` |
| 생성 (장면 클립 만들기) | `artifacts.py` `GeneratedClip` | `ports/generator.py` `VideoGenerator` · `pipeline/stages.py` `GenerateStage`, `pipeline/requests.py` | `adapters/generator/` |
| 최종 영상 조립 | `artifacts.py` `VideoArtifact` | `ports/assembler.py` `VideoAssembler` | `adapters/assembler/` `FfmpegVideoAssembler` |
| 부모 클립 편집 | — | `ports/generator.py` `ParentClip`·`RevisionDirective` | `adapters/generator/openrouter/` (흐름 `generator.py`, 편집 지시문 `_prompts.py`) |
| 재사용 판단 (해시·키) | `ir.py` 의 fingerprint, `hashing.py` | `ports/generator.py` `generation_fingerprint`·`result_key` | `adapters/generator/cache.py`, `ledger.py` |
| 정체성 기준 이미지 | `services/identity.py`, `models.py` `ReferenceImage` | `ports/identity.py` `IdentityReferenceExtractor` · `pipeline/identity_anchoring.py` `IdentityAnchoring` | `adapters/identity/` |
| 검수 (요구 반영 · 유지 요소 · 일관성 · 보존) | `checks.py` `CheckResult`, `metrics.py`, `services/comparison.py` | `ports/checker.py` `Checker`, `checks.py` `CheckSuite` · `pipeline/stages.py` `CheckStage` | `adapters/checker/` |
| 수정 의도 · 변경 계획 | `revision/` — 수정 의도 `intent.py` `RevisionIntent`, 속성 변경 `changes.py`, 전파 `propagation.py`, 수정 요청 풀기 `resolution.py` | `ports/change_request.py` · `use_cases/create_revision.py`, `plan_revision.py` | `adapters/change_request/` |
| 추적 | `tracking.py` | `ports/tracker.py` `ObjectTracker` · `pipeline/stages.py` `TrackStage` | `adapters/tracker/` |
| 테마 · 시리즈 (같은 테마의 편) | `series/theme/` `Theme`, `series/episode/` `EpisodePlan`, 편의 기록 `series/record.py`, 숏폼 규격 판정 `series/shortform.py`, 원본에서 읽은 것 `series/source_analysis.py` | `ports/series/` · `use_cases/series/` — `extract_theme.py`, `create_episode.py`, `adopt_anchors.py`, `revise_episode.py`, `settle_episode.py` | `adapters/series/` (포트마다 하위 패키지: `source_analysis/`, `theme_extraction/`, `planning/`, `store/`), 조립 `config/theme_wiring.py` · `theme_adapters.py` · `theme_settings.py` |
| 화면 문구 · 화면 국소 수정 | `series/screen_correction/`, 화면 좌표 `screen.py` `Region` | `ports/series/overlay.py` `OverlayRenderer`·`ScreenTextReader` · `use_cases/series/render_episode.py`, `check_episode_screen.py`, `correct_episode_screen.py` | `adapters/series/overlay/` |
| 음성 인식 · 녹음 붙이기 | 받아쓰기 `speech.py` `Transcript`, 녹음 맞추기 `series/narration.py` | `ports/speech.py` `SpeechRecognizer`, `ports/sound.py` `SoundReader`, `ports/series/audio.py` `AudioEditor` · `use_cases/series/attach_narration.py` | `adapters/speech/`, `adapters/series/audio/` |
| 대본 (소리 → 줄 · 근거 · 대본만 고치기) | `script/` `SpokenScript`, `series/script_evidence.py`, `script_correction.py`, `script_revision.py` | `use_cases/script/` `ExtractScript`·`listen` · `use_cases/series/correct_source_script.py`, `revise_episode_script.py` | 소리 읽기와 음성 인식 어댑터를 쓴다 (위 줄) |
| 영상 처리 (ffmpeg) | 영상의 겉모습 `media.py` `MediaInfo` | — | `adapters/shared/media/` — 찾기·실행 `ffmpeg.py`, 프레임 뽑기 `frames.py` (`FrameSampler`), 영상 정보 `probe.py`, 유사도(SSIM) `similarity.py`, 시리즈·음성 어댑터의 읽기 `media_tools.py` (영상·소리 정보 · 컷 · 무음 · 영역 SSIM), 가짜 미디어 형식 `fake_media.py`. 클립 정규화는 `adapters/generator/normalizer.py` |
| LLM·VLM 과 API 키 | — | `ports/llm.py` `LLMClient`·`LLMKeySource` | `adapters/llm/`, 응답을 물어본 것에 맞추기 `adapters/shared/vlm/answers.py`, 키 파일 읽기·키 표시 `adapters/shared/api_keys.py` |
| 저장 (산출물 · DB) | `artifacts.py` | `ports/artifact_store.py`, `ports/repositories.py`, `ports/unit_of_work.py` | `adapters/storage/`, `adapters/persistence/` |
| 작업 실행 · 복구 | `state_machine.py` | `ports/jobs.py` `JobDispatcher` · `use_cases/run_generation_pipeline.py`, `recover_pending_versions.py` · `pipeline/` | `adapters/jobs/`, `presentation/recovery.py` |
| HTTP API | — | `dto.py`, `use_cases/queries.py` | `presentation/` |
| 조립 (설정 → 어댑터) | — | — | `config/` — 설정 `settings.py`, 키 → 어댑터 `registrations/<포트>.py`, 엮기 `container.py`. 테마·시리즈는 `theme_settings.py` · `theme_adapters.py` · `theme_wiring.py` (`Container.series`) |
| 성공 지표 평가 | `metrics.py` | — | `evaluation/` — 지표 1 (수정 케이스) `edit_cases.py` · `edit_runner.py` · `edit_report.py` · `edit_metrics.py`, 구조화 골드셋 `structuring/`. 실행은 `tools/evaluate_edits.py` · `evaluate_structuring.py` |
| 실행 도구 (시연 런처 · 번들) | — | — | `tools/`, `scripts/` |

## 2. 패키지 목록

패키지마다 맡는 개념과 밖에서 쓰는 이름이다. 안쪽 구현은 각 패키지의 `__init__.py` 설명을 본다.

| 패키지 | 맡는 개념 | 밖에서 쓰는 이름 |
|---|---|---|
| `src/frame/` | 패키지 최상위 | `__version__` |
| `src/frame/domain/` | 업무 규칙. 표준 라이브러리만 쓴다 | `IntermediateRepresentation`, `VideoVersion`, `RevisionIntent`, `CheckResult`, 열거형(`enums.py`), 오류(`errors.py`). 여러 기능이 함께 쓰는 일반 타입: 영상의 겉모습 `media.py` `MediaInfo`, 화면 좌표 `screen.py` `Region`, 받아쓰기 `speech.py` `Transcript` |
| `src/frame/domain/revision/` | 수정 의도와 그 규칙 — 수정 요청을 CHANGE·REPAIR 로 풀고, 변경을 중간표현에 반영하고, 유지할 요소와 함께 남긴다 | `RevisionIntent`, `KeepItem`, `AttributeChange`, `ChangePropagationPolicy`·`DefaultPropagationPolicy`, `target_scenes`·`repair_targets`, `resolution` 의 `resolve_revision`·`revision_draft` |
| `src/frame/domain/services/` | 여러 엔티티에 걸친 규칙 | 정체성 기준 `identity`, 버전 비교 `comparison` |
| `src/frame/domain/script/` | 대본 — 소리에서 뽑아 줄 단위로 다듬은 말과 한국어 수량 (ADR-0028). 시리즈 기능 밖에서도 쓰는 개념이다 | `SpokenScript`, `SpokenLine`, `build_script`, `ScriptPolicy`, `Quantity`, `quantities`·`quantity_conflicts`, `parse_number`·`read_number` |
| `src/frame/domain/series/` | 시리즈 — 숏폼 한 편에서 뽑은 테마로 같은 테마의 편을 이어 만드는 기능의 도메인 (ADR-0025~0029) | 입구는 내보내지 않는다. 모듈로 import 한다: `source_analysis` (원본에서 읽은 것), `shortform` (숏폼 규격 판정), `record` (편의 기록), `narration` (녹음 붙이기), `script_evidence` · `script_correction` · `script_revision` (대본을 근거로 쓰기 · 뽑은 줄 고치기 · 녹음 대본 고치기) |
| `src/frame/domain/series/theme/` | 테마 — 편 사이의 규칙: 고정 출연 · 형식(장면 자리) · 스타일 · 문구 틀 · 내레이션 | `Theme`, `FormatSpec`·`SceneSlot`, `CastMember`, `StyleSpec`, `TextRule`·`ThemeVariable`, `NarrationSpec`, `SourceVideo`, `Condition` |
| `src/frame/domain/series/episode/` | 편 계획 — 테마의 고정 요소와 이번 편의 내용을 합친 것 (IR · 녹음 대본 · 화면 문구) | `EpisodePlan`, `EpisodeBrief`, `EpisodeContent`, `ScriptLine`, `OverlayItem`, `build_episode_plan` |
| `src/frame/domain/series/screen_correction/` | 화면 국소 수정 — 어긋난 곳의 종류, 찾기, 고치는 걸음 (순수 함수) | `ScreenIssue`, `IssueKind`, `CorrectionMethod`, `issues_from_checks`, `plan_text_issues`, `narration_issues`, `fix_overlay_text`, `patch_scene_text`, `repair_axis`, `CorrectionStep` |
| `src/frame/application/` | 유스케이스와 포트 | `CheckSuite`, `dto.py` 의 명령·결과, 어댑터 오류 계층 `errors.py` |
| `src/frame/application/ports/` | 바깥에 요구하는 계약 | `RequirementStructurer`, `VideoGenerator`, `VideoAssembler`, `IdentityReferenceExtractor`, `ObjectTracker`, `Checker`, `ChangeRequestInterpreter`, `LLMClient`, `ArtifactStore`, 저장소·`UnitOfWork`, `JobDispatcher`, 음성 인식 `speech.py` `SpeechRecognizer`, 소리 읽기 `sound.py` `SoundReader` |
| `src/frame/application/ports/series/` | 시리즈 기능의 포트 (구현은 `adapters/series/`) | 입구는 내보내지 않는다. 모듈로 import 한다: `source_analysis` (`MediaProber`, `ShotDetector`), `theme_extraction` (`ThemeExtractor`), `planning` (`SeriesPlanner`), `audio` (`AudioEditor`), `overlay` (`OverlayRenderer`, `ScreenTextReader`), `repositories` (`ThemeRepository`, `EpisodeRepository`) |
| `src/frame/application/use_cases/` | 유스케이스 하나가 모듈 하나 | `CreateProject`, `StructureRequirements`, `EditDraftIr`, `UploadReferenceImage`, `AcceptGeneration`, `RunGenerationPipeline`, `CreateRevision`, `PlanRevision`, `ProjectQueries`, `RecoverPendingVersions` |
| `src/frame/application/use_cases/script/` | 대본 유스케이스 — 영상·음성 파일의 소리에서 대본을 뽑는다 (ADR-0028) | `ExtractScript`, `listen`, `Heard`, `ListeningPolicy`, `MAX_MEDIA_BYTES`·`MAX_MEDIA_MS` |
| `src/frame/application/use_cases/series/` | 시리즈 유스케이스 — 테마 뽑기, 편 만들기, 녹음 붙이기, 화면 문구 얹기, 어긋난 곳 고치기, 대본만 고치기 | `ExtractTheme`, `CreateEpisode`, `AdoptAnchors`, `AttachNarration`, `RenderEpisode`, `CheckEpisodeScreen`, `CorrectEpisodeScreen`, `ReviseEpisode`, `SettleEpisode`, `CorrectSourceScript`, `ReviseEpisodeScript` |
| `src/frame/application/pipeline/` | 생성 → 추적 → 검수 단계와 실행기 | `PipelineRunner`, `GenerateStage`, `TrackStage`, `CheckStage`, `PipelineContext` |
| `src/frame/adapters/` | 포트의 구현. 하위 패키지 하나가 개념 하나를 맡는다. 최상위에는 모듈을 두지 않는다 — 함께 쓰는 것은 `shared/` 에 둔다 (ADR-0031) | — |
| `src/frame/adapters/shared/` | 어댑터 패키지들이 함께 쓰는 것 (어댑터끼리는 서로 import 하지 않는다) | `api_keys` (`find_key`, `mask_key`), `http` (`HttpTransport`, `UrllibTransport`, `HttpResponse`, `TransportError`), `runtime` (ID 생성기 `UuidIdGenerator`·`SequentialIdGenerator`, 시계 `SystemClock`·`FixedClock`), `locking` (`KeyedLocks`) |
| `src/frame/adapters/shared/media/` | 영상 처리 도구 (ffmpeg)와 가짜 미디어 형식 | `ffmpeg` (`resolve_ffmpeg`, `require_ffmpeg`, `run_ffmpeg`, `run_reading`, `tail`, `FfmpegNotRun`, `VideoToolError`), `frames` (`FrameSampler`, `midpoints`, `extract_frame`, `Box`, `Image`), `probe` (`video_size`, `probe_video`, `VideoProbe`, 프레임 세기 `read_video_packets`), `similarity` (`ssim`), `media_tools` (`probe_media`, `detect_cuts`, `detect_silences`, `region_ssim`, `MediaToolError` — 시리즈·음성 어댑터가 쓴다. 영상 정보 읽기와 오류 종류를 위의 것과 한 벌로 묶는 일이 남았다), `fake_media` (`fake_video`, `fake_recording`, `decode_fake_media`) |
| `src/frame/adapters/shared/vlm/` | 이미지를 읽는 모델(VLM)에 묻고 답 읽기 | `ask` (`ask_json`), `answers` (`match_answers`, `requested_key`), `wording` (`predicate_words`, `described`) |
| `src/frame/adapters/structurer/` | 구조화 | `RuleBasedStructurer`, `LLMStructurer`, `FakeStructurer` |
| `src/frame/adapters/generator/` | 장면 클립 생성, 클립 정규화, 생성 캐시·작업 원장 | `OpenRouterVideoGenerator`, `FakeVideoGenerator`, `CachingVideoGenerator`, `RetryingVideoGenerator`, `TimeoutVideoGenerator`, `DelegatingVideoGenerator` (데코레이터의 바탕), `normalizer` 의 `FfmpegVideoNormalizer` |
| `src/frame/adapters/generator/openrouter/` | 제공자 OpenRouter 의 생성기: 제출 · 대기 · 원장으로 이어 받기 · 부모 클립 편집 · 미리보기 | `OpenRouterVideoGenerator`, `resolve_openrouter_key`, `output_size`, `compile_video_prompt`·`compile_edit_prompt`, `VideoModelInfo` |
| `src/frame/adapters/assembler/` | 최종 영상 조립 — 장면 클립을 이어 영상 하나로 | `FfmpegVideoAssembler`, `FakeVideoAssembler` |
| `src/frame/adapters/identity/` | 정체성 기준 이미지 뽑기 | `VlmCropIdentityExtractor`, `FrameIdentityExtractor`, `FakeIdentityExtractor` |
| `src/frame/adapters/checker/` | 검수 | `VlmRequirementChecker`, `VlmConsistencyChecker`, `VlmPreservationChecker`, `SsimPreservationChecker`, 가짜 검수기 셋 |
| `src/frame/adapters/change_request/` | 글로 쓴 변경 요청의 해석 | `LLMChangeRequestInterpreter`, `RuleBasedChangeRequestInterpreter` |
| `src/frame/adapters/llm/` | LLM·VLM 클라이언트와 키 저장소 | `OpenAIJsonClient`, `ReplayLLMClient`, `LLMKeyStore` |
| `src/frame/adapters/tracker/` | 객체 추적 | `FakeObjectTracker`, `OpenCvObjectTracker` (뼈대) |
| `src/frame/adapters/storage/` | 산출물 저장소 | `LocalFileArtifactStore`, `InMemoryArtifactStore` |
| `src/frame/adapters/persistence/` | 저장소와 저장 단위 — 메모리 구현 | `InMemoryUnitOfWork`, `in_memory_uow_factory` |
| `src/frame/adapters/persistence/sqlalchemy/` | 저장소와 저장 단위 — SQLAlchemy 구현 | `SqlAlchemyUnitOfWork`, `sqlalchemy_uow_factory`, `create_engine_from_url` |
| `src/frame/adapters/jobs/` | 작업 디스패처 | `InlineJobDispatcher`, `ThreadPoolJobDispatcher`, `RecordingJobDispatcher` |
| `src/frame/adapters/speech/` | 음성 인식 — 말소리 → 낱말 시각이 붙은 받아쓰기. 시리즈 기능 밖에서도 쓰는 개념이다 (TTS 는 없다) | `FakeSpeechRecognizer`. 조립이 고를 때 import 하는 것: `openai` 의 `OpenAISpeechRecognizer`, `faster_whisper` 의 `FasterWhisperRecognizer` (선택 설치 `speech-local`), `caching` 의 `CachingSpeechRecognizer` |
| `src/frame/adapters/series/` | 시리즈 기능의 어댑터 — 포트마다 하위 패키지 하나. 입구는 테스트·오프라인용 구현만 내보내고, 실제 구현(ffmpeg · LLM)은 조립이 고를 때 import 한다 | — (아래 패키지를 import 한다) |
| `src/frame/adapters/series/source_analysis/` | 원본 영상 분석 — 겉모습과 컷의 시각 (모델을 쓰지 않는다) | `FakeMediaProber`, `FakeShotDetector`. `ffmpeg` 의 `FfmpegMediaProber`·`FfmpegShotDetector` |
| `src/frame/adapters/series/theme_extraction/` | 테마 추출 — 분석 결과와 영상에서 테마 · 원본 IR · 화면 문구 | `FakeThemeExtractor`. `vlm` 의 `VlmThemeExtractor` |
| `src/frame/adapters/series/planning/` | 편 기획 — 테마와 편 요청에서 이번 편의 내용 | `TemplateSeriesPlanner`. `llm` 의 `LLMSeriesPlanner` |
| `src/frame/adapters/series/audio/` | 소리 편집 — 소리 뽑기, 무음 재기, 녹음을 잘라 장면에 놓기, 영상에 트랙 붙이기. 소리 읽기 포트도 여기서 구현한다 | `FakeAudioEditor`. `ffmpeg` 의 `FfmpegAudioEditor` |
| `src/frame/adapters/series/overlay/` | 후반 합성 — 화면 문구 얹기와 화면의 글자 읽기 | `FakeOverlayRenderer`, `FakeScreenTextReader`. `ffmpeg_renderer` 의 `FfmpegOverlayRenderer`, `vlm_reader` 의 `VlmScreenTextReader`, `caching_reader` 의 `CachingScreenTextReader` |
| `src/frame/adapters/series/store/` | 테마·편 저장 — 문서 하나씩 두고, 편마다 읽고 고쳐 덮어쓰는 사이를 잠근다 | `InMemoryThemeRepository`, `InMemoryEpisodeRepository`, `ArtifactThemeRepository`, `ArtifactEpisodeRepository` |
| `src/frame/config/` | 조립 지점 | `Settings`, `AdapterRegistry`, `default_registry`, `Container`, `build_container`. 테마·시리즈: `ThemeSettings` (`theme_settings.py`), `build_theme_services` (`theme_wiring.py`), 어댑터 고르기 `theme_adapters.py` |
| `src/frame/config/registrations/` | 기본 등록 — 설정 키가 어느 어댑터가 되는지 (모듈 하나가 포트 하나) | `register_defaults`, 포트별 `register` (`llm`, `structurer`, `change_request`, `generator`, `assembler`, `identity`, `tracker`, `checker`, `storage`, `persistence`, `jobs`) |
| `src/frame/presentation/` | HTTP API | `create_app`, 스키마 `schemas.py`, 응답 변환 `mappers.py`, 오류 응답 `errors.py` |
| `src/frame/presentation/routers/` | 자원별 라우터 | `projects`, `versions`, `reference_images`, `settings`, `vocabulary` |
| `src/frame/evaluation/` | 성공 지표 평가 — 지표 1 (단일 속성 수정 케이스의 성공률 · 비대상 변경률, ADR-0022) | `evaluate_case`, `evaluate_cases`. 모듈로 import 하는 것: `edit_cases` (`EditCaseSpec`, `load_cases`), `edit_runner` (`run_case`, `CaseRun`), `edit_report` (`judge`, `build_report`) |
| `src/frame/evaluation/structuring/` | 구조화 골드셋 평가 — 구조화 결과를 기대 IR(골드)과 견주어 채점하고, 실행을 기록·비교한다 | `GoldCase`·`load_gold_cases`, `score_case`, `summarize`, `run_gold_set`, `build_record`·`save_record`·`read_record`, `rescore_record`, `compare_records`, `format_summary`·`format_comparison` |
| `src/frame/tools/` | 명령줄 도구 | `demo` (시연 런처), `bundle` (번들 빌드), `export_openapi`, `structure_cli`, `evaluate_edits` (지표 1 평가), `evaluate_structuring` (골드셋 기록 비교 · 다시 채점) |
