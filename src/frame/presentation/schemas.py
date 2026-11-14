"""HTTP 요청/응답 스키마 (pydantic). 도메인 객체는 presentation/mappers.py 에서 변환한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

# ---- 입력 제한 (공개 데모에서 LLM 비용·저장 용량을 지키기 위한 상한) ----------------

MAX_PROMPT_CHARS = 4000
MAX_REFERENCE_IMAGES = 10
MAX_TARGET_DURATION_MS = 300_000  # 5분 (숏폼 대상)
# IR 문서 (블록 입력·DRAFT IR 수정). 장면 길이 합계도 MAX_TARGET_DURATION_MS 를 넘을 수 없다 (생성 비용 보호)
MAX_IR_ENTITIES = 50
MAX_IR_SCENES = 60
MAX_SCENE_REQUIREMENTS = 50
MIN_SCENE_MS = 500  # 보정이 장면 길이를 이 값보다 짧게 두지 않는다 (adapters/structurer/repair.py)

# ---- 요청 ----------------------------------------------------------------


class ReferenceImageIn(BaseModel):
    image_uri: str = Field(min_length=1, max_length=1024)
    entity_key: str | None = Field(default=None, max_length=64)


class IREntityIn(BaseModel):
    entity_key: str = Field(
        min_length=1, max_length=64, description="영문 snake_case 식별자 (요구조건·추적의 키)"
    )
    kind: str = Field(
        min_length=1,
        max_length=32,
        description="PERSON | ANIMAL | PROP | BACKGROUND | TEXT | OTHER (person 같은 별칭은 서버가 고친다)",
    )
    description: str = Field(default="", max_length=500)  # 생성 지시문에 들어가는 외형 설명
    # 화면에 보여 줄 이름 ("컵 01", ADR-0022). 생성 조건이 아니므로 바꿔도 장면을 다시 만들지 않는다
    display_name: str | None = Field(default=None, max_length=64)


class IRRequirementIn(BaseModel):
    scene_key: str | None = Field(default=None, max_length=64)  # 생략하면 그 요구조건이 속한 장면
    entity_key: str | None = Field(default=None, max_length=64)  # null 이면 장면 수준 조건
    related_entity_key: str | None = Field(default=None, max_length=64)  # 관계 술어의 상대 객체
    predicate: str = Field(min_length=1, max_length=64)  # GET /api/vocabulary/predicates
    expected_value: str = Field(default="", max_length=300)  # 관계 술어는 비우면 상대 객체 키


class IRSceneIn(BaseModel):
    scene_key: str = Field(min_length=1, max_length=64)
    planned_start_ms: int = Field(ge=0, le=MAX_TARGET_DURATION_MS)
    planned_end_ms: int = Field(gt=0, le=MAX_TARGET_DURATION_MS)
    description: str = Field(default="", max_length=1000)
    entity_keys: list[str] = Field(default_factory=list, max_length=MAX_IR_ENTITIES)
    requirements: list[IRRequirementIn] = Field(
        default_factory=list, max_length=MAX_SCENE_REQUIREMENTS
    )


class IRIn(BaseModel):
    """IR 문서. GET /api/versions/{id} 의 ir 과 같은 모양이며,
    읽기 전용 필드(entity_id·requirement_key·clip_url 등)는 무시한다.

    서버가 결정적으로 보정한 뒤 검증한다: 장면 구간을 0 ms 부터 빈틈없이 이어 붙이고(길이는 유지),
    요구조건이 가리키는 객체를 장면에 연결하고, 객체 종류의 별칭을 고치고,
    정의되지 않았거나 배경을 가리키는 관계를 뺀다. 고친 것은 응답의 repair_notes 로 알려 준다.
    고칠 수 없는 문서(키 중복·빈 값 등)는 422 VALIDATION_ERROR.
    """

    schema_version: int = 1
    entities: list[IREntityIn] = Field(default_factory=list, max_length=MAX_IR_ENTITIES)
    scenes: list[IRSceneIn] = Field(min_length=1, max_length=MAX_IR_SCENES)

    @model_validator(mode="after")
    def _total_duration_within_limit(self) -> IRIn:
        total = sum(max(s.planned_end_ms - s.planned_start_ms, MIN_SCENE_MS) for s in self.scenes)
        if total > MAX_TARGET_DURATION_MS:
            raise ValueError(
                f"장면 길이 합계 {total} ms 가 상한 {MAX_TARGET_DURATION_MS} ms 를 넘습니다"
            )
        return self


class CreateProjectIn(BaseModel):
    """요구사항은 글(prompt) 또는 블록으로 조립한 IR 문서(ir) 로 준다. 둘 중 하나는 있어야 한다.

    - prompt 만: 구조화를 시작한다 (202, state=STRUCTURING). 끝나면 DRAFT 또는 FAILED.
    - ir 이 있으면: LLM 없이 보정·검증만 거쳐 바로 DRAFT 를 만든다 (201, repair_notes 에 보정 내용).
      prompt 를 함께 주면 원문(source_prompt)으로만 보관한다.
    """

    title: str = Field(min_length=1, max_length=255)
    prompt: str | None = Field(default=None, min_length=1, max_length=MAX_PROMPT_CHARS)
    ir: IRIn | None = None  # 블록 입력 화면이 조립한 IR 문서 (#10)
    reference_images: list[ReferenceImageIn] = Field(
        default_factory=list, max_length=MAX_REFERENCE_IMAGES
    )
    target_duration_ms: int | None = Field(default=None, gt=0, le=MAX_TARGET_DURATION_MS)
    seed: int | None = None

    @model_validator(mode="after")
    def _prompt_or_ir(self) -> CreateProjectIn:
        if self.prompt is None and self.ir is None:
            raise ValueError("요구사항 글(prompt)과 IR 문서(ir) 가운데 하나는 있어야 합니다")
        return self


KeepAspectName = Literal["APPEARANCE", "ACTION", "SHAPE", "BACKGROUND", "LIGHTING", "COMPOSITION"]
MAX_REQUEST_TEXT_CHARS = 500


class KeepItemIn(BaseModel):
    """다시 만드는 장면에서 그대로 둘 요소 (ADR-0017).

    객체 요소(APPEARANCE 모습 · ACTION 동작 · SHAPE 형태)는 entity_key 가 필요하고,
    장면 요소(BACKGROUND 배경 · LIGHTING 조명 · COMPOSITION 구도)는 entity_key 를 비운다.
    """

    aspect: KeepAspectName
    entity_key: str | None = Field(default=None, min_length=1, max_length=64)


class CreateRevisionIn(BaseModel):
    # None 이면 장면 수준 조건(장소·조명·분위기·카메라)을 바꾼다
    entity_key: str | None = Field(default=None, min_length=1, max_length=64)
    predicate: str = Field(min_length=1, max_length=64)
    # CHANGE: 새 값 / REPAIR: 지켜져야 하는 값 (지금 요구조건의 값과 같아야 한다)
    new_value: str = Field(min_length=1, max_length=300)
    scope: Literal["SCENES", "ALL_APPEARANCES"] = "ALL_APPEARANCES"
    scene_keys: list[str] = Field(default_factory=list, max_length=50)
    # 같은 술어 조건이 여러 개(관계)일 때 바꿀 대상의 현재 값
    current_value: str | None = Field(default=None, max_length=300)
    # CHANGE: 요구조건의 값을 바꾼다 / REPAIR: 값은 그대로 두고 그 요구조건이 걸린 장면을 다시 만든다
    kind: Literal["CHANGE", "REPAIR"] = "CHANGE"
    keep: list[KeepItemIn] = Field(default_factory=list, max_length=20)
    # 사용자가 쓴 요청 원문. 기록·표시용이며 생성에는 쓰지 않는다
    request_text: str | None = Field(default=None, max_length=MAX_REQUEST_TEXT_CHARS)


class RevisionPlanIn(BaseModel):
    """변경 계획 미리보기 요청 (ADR-0019). 구조화된 값(predicate + new_value)을 주거나 request_text 만 준다.

    request_text 만 주면 서버가 해석한다. 화면에서 고른 객체·범위·유지 요소를 함께 주면 그 값이 우선한다.
    """

    request_text: str | None = Field(default=None, max_length=MAX_REQUEST_TEXT_CHARS)
    entity_key: str | None = Field(default=None, min_length=1, max_length=64)
    predicate: str | None = Field(default=None, min_length=1, max_length=64)
    new_value: str | None = Field(default=None, min_length=1, max_length=300)
    scope: Literal["SCENES", "ALL_APPEARANCES"] | None = None
    scene_keys: list[str] = Field(default_factory=list, max_length=50)
    current_value: str | None = Field(default=None, max_length=300)
    kind: Literal["CHANGE", "REPAIR"] | None = None  # 생략하면 서버가 정한다
    keep: list[KeepItemIn] = Field(default_factory=list, max_length=20)


class LLMKeyIn(BaseModel):
    """화면에서 입력한 OpenAI API 키. SecretStr 이라 repr·로그에 값이 나오지 않는다."""

    api_key: SecretStr


# ---- 응답 ----------------------------------------------------------------


class LLMKeyStatusOut(BaseModel):
    configured: bool
    source: Literal["runtime", "config", "env", "file"] | None  # runtime = 화면 입력(메모리)
    hint: str | None  # 끝 4자리만 (예: sk-...abcd)
    runtime_entry: bool  # 이 요청을 보낸 기기에서 키를 입력할 수 있는지 (켜져 있고 같은 기기·원격 허용)
    runtime_entry_enabled: bool  # 서버가 화면 키 입력을 켰는지 (FRAME_LLM_RUNTIME_KEY)


class ErrorDetailOut(BaseModel):
    loc: str  # 예: body.title
    message: str


class ErrorOut(BaseModel):
    """모든 오류 응답의 공통 형식."""

    error_code: str
    error_message: str
    details: list[ErrorDetailOut] | None = None  # 요청 스키마 검증 오류일 때만


class HealthAdaptersOut(BaseModel):
    structurer: str
    generator: str
    tracker: str
    checkers: list[str]
    identity: str = "off"  # 정체성 기준 이미지 추출기 (off 면 쓰지 않음)
    # 글로 쓴 변경 요청의 해석기: rule_based(DSL 형식만) | llm(자연어)
    change_interpreter: str = "rule_based"
    persistence: str
    jobs: str
    llm_provider: str | None  # structurer=llm 일 때만
    llm_model: str | None


class HealthOut(BaseModel):
    status: str
    version: str
    # 화면이 예시 입력을 고를 때 쓴다: dsl(규칙 기반) / natural_language(LLM) / any
    structuring_input: Literal["dsl", "natural_language", "any"]
    adapters: HealthAdaptersOut
    llm_key: LLMKeyStatusOut | None  # LLM 구조화(openai)를 쓸 때만


class PredicateOut(BaseModel):
    name: str
    value_kind: str
    relational: bool
    aliases: list[str]
    description: str


class VocabularyOut(BaseModel):
    predicates: list[PredicateOut]


class CreateProjectOut(BaseModel):
    project_id: str
    version_id: str
    state: str  # 글: STRUCTURING (202) / IR 직접 입력: DRAFT (201)
    # IR 직접 입력에서 서버 보정이 고친 것 (장면 구간 이동·객체 연결 등). 글로 접수하면 빈 목록
    repair_notes: list[str] = Field(default_factory=list)


class VersionSummaryOut(BaseModel):
    version_id: str
    state: str
    parent_version_id: str | None
    created_at: datetime


class ProjectOut(BaseModel):
    project_id: str
    title: str
    versions: list[VersionSummaryOut]


class RequirementOut(BaseModel):
    requirement_key: str
    scene_key: str
    entity_key: str | None
    related_entity_key: str | None
    predicate: str
    expected_value: str
    value_kind: str | None  # 어휘에 등록된 술어면 COLOR/TEXT/NUMBER/POSITION/ENTITY
    relational: bool


class ClipProductionOut(BaseModel):
    """이 장면의 클립이 어떻게 만들어졌는가 (생성기가 남긴 기록). 부모 클립을 그대로 쓴 장면은 부모 때의 기록이다."""

    # GENERATED: 새로 생성 / EDITED: 부모 클립을 편집해서 만듦 (ADR-0016)
    method: Literal["GENERATED", "EDITED"]
    model: str | None = None
    cost_usd: float | None = None
    seconds: float | None = None  # 제공자 쪽에서 만드는 데 걸린 시간
    # 편집하려 했지만 새로 생성했을 때의 사유 (예: 부모 작업의 저장본 주소를 얻지 못함). 화면이 알려 줄 수 있다
    edit_fallback_reason: str | None = None
    # 편집하지 못한 사정이 일시적이어서 새로 생성한 임시 결과인가 (ADR-0021).
    # true 면 부모 버전에서 같은 수정을 다시 실행했을 때 편집으로 만들 수 있다 (계획으로 먼저 확인한다)
    provisional: bool = False


class SceneOut(BaseModel):
    scene_key: str
    planned_start_ms: int
    planned_end_ms: int
    description: str
    entity_keys: list[str]
    requirements: list[RequirementOut]
    clip_uri: str | None  # 내부 저장소 URI (디버깅용)
    clip_url: str | None  # 브라우저가 재생·다운로드할 HTTP 경로
    clip_media_type: str | None
    clip_production: ClipProductionOut | None = None  # 기록이 없으면 null


class EntityOut(BaseModel):
    entity_id: str
    entity_key: str
    kind: str
    description: str
    # 화면에 보여 줄 이름 (ADR-0022). null 이면 화면이 종류와 순번으로 라벨을 만든다 ("컵 01")
    display_name: str | None = None


class IROut(BaseModel):
    entities: list[EntityOut]
    scenes: list[SceneOut]


class ReferenceImageOut(BaseModel):
    reference_image_id: str
    image_uri: str
    # 브라우저가 볼 수 있는 HTTP 경로. 저장소에 있는 이미지일 때만 (객체 썸네일·객체별 전후 비교에 쓴다)
    image_url: str | None = None
    entity_key: str | None
    # USER: 사용자가 올린 참조 / DERIVED: 장면 클립에서 뽑은 객체의 정체성 기준 이미지 (ADR-0015)
    origin: Literal["USER", "DERIVED"] = "USER"
    source_scene_key: str | None = None  # 정체성 기준을 뽑은 장면
    description: str | None = None  # 기준 이미지에서 읽은 외형 설명
    # 이 버전의 조건에 맞는 기준인가. 수정으로 외형 조건이 바뀐 객체는 예전 기준(false)과 새 기준(true)이 함께 남는다.
    # 객체 카드의 썸네일은 true 인 것을 쓴다 (사용자 참조는 늘 true)
    is_current: bool = True


class KeepItemOut(BaseModel):
    aspect: str  # APPEARANCE | ACTION | SHAPE | BACKGROUND | LIGHTING | COMPOSITION
    entity_key: str | None  # 장면 요소면 null


class RevisionIntentOut(BaseModel):
    """수정 버전이 하려는 것 (ADR-0017). 원본 버전에는 없다."""

    kind: str  # CHANGE | REPAIR
    entity_key: str | None  # null 이면 장면 수준 조건
    predicate: str
    value: str  # CHANGE: 새 값 / REPAIR: 지켜져야 하는 값
    previous_value: str | None  # CHANGE: 이전 값 (장면마다 다르면 null)
    scope: str  # SCENES | ALL_APPEARANCES
    target_scene_keys: list[str]  # 다시 만드는 장면
    keep: list[KeepItemOut]
    request_text: str | None


class StageProgressOut(BaseModel):
    stage: str  # generate | track | check
    status: Literal["RUNNING", "COMPLETED", "FAILED"]
    elapsed_ms: int  # 단계 시작부터 마지막 기록까지 (끝났으면 단계 소요 시간)
    done: int  # 끝낸 항목(장면) 수
    total: int  # 전체 항목 수. 0 이면 항목 단위 진행이 없는 단계 (check)
    current: str | None  # 처리 중인 장면 키. 실패했으면 실패한 장면


class ProgressOut(BaseModel):
    """생성·추적·검수 진행. 처리 중에도 장면 단위로 갱신되어 폴링으로 볼 수 있다 (실행 전 버전은 null)."""

    stage: str | None  # 마지막으로 시작한 단계 (처리 중·완료·실패)
    elapsed_ms: int  # 시작한 단계들의 소요 시간 합
    stages: list[StageProgressOut]  # 시작한 단계만, 순서대로


class VersionOut(BaseModel):
    version_id: str
    project_id: str
    parent_version_id: str | None
    state: str
    source_prompt: str | None
    seed: int | None
    frozen_fingerprint: str | None
    ir: IROut | None
    video_uri: str | None  # 내부 저장소 URI (디버깅용)
    video_url: str | None  # 브라우저가 재생할 HTTP 경로 (GET 시 Range 지원)
    video_media_type: str | None  # 실제 모델: video/mp4, Fake: application/json
    error_code: str | None
    error_message: str | None
    reference_images: list[ReferenceImageOut]
    created_at: datetime
    progress: ProgressOut | None  # 실패한 버전도 어느 단계·장면까지 했는지 남는다 (이슈 #3)
    revision: RevisionIntentOut | None = None  # 수정 버전이면 무엇을 하려는 수정인지 (ADR-0017)


class EditDraftIrOut(VersionOut):
    """PUT /api/versions/{version_id}/ir — 고친 뒤의 버전 (GET 과 같은 모양) 과 보정 메모."""

    # 서버가 고친 것 (장면 구간 이동·객체 연결·종류 별칭 등). 없으면 빈 목록. 화면은 ir 로 블록을 다시 그린다
    repair_notes: list[str]


class CreateRevisionOut(BaseModel):
    version_id: str
    parent_version_id: str
    state: str
    changed_scene_keys: list[str]  # 다시 만들 장면 (CHANGE: 조건이 바뀐 장면, REPAIR: 다시 맞출 장면)
    kind: str = "CHANGE"  # CHANGE | REPAIR


class PlannedProductionOut(BaseModel):
    """이 장면을 지금 실행하면 어떻게 만들어질지 (생성기의 미리보기, ADR-0020). 실행 뒤의 사실은 clip_production."""

    method: Literal["GENERATE", "EDIT"]  # 새로 생성 | 부모 클립을 편집
    model: str | None = None
    cost_usd: float | None = None  # 그 방식의 어림 비용 (모르면 null)
    seconds: float | None = None  # 그 방식으로 만든 기록이 있을 때의 어림 시간
    # 편집하도록 설정됐지만 지금은 편집할 수 없어 새로 생성하게 되는 사유. 있으면 화면이 알려 준다
    # (예: 부모 영상의 저장본 주소가 아직 없음 — 잠시 뒤 계획을 다시 확인하면 편집으로 바뀔 수 있다)
    edit_unavailable_reason: str | None = None


class RevisionPlanSceneOut(BaseModel):
    scene_key: str
    planned_start_ms: int
    planned_end_ms: int
    will_regenerate: bool  # 이 수정으로 다시 만드는 장면인가
    current_value: str | None  # 지금 그 조건의 값 (그 장면에 조건이 없으면 null)
    verdict: str | None  # 이 버전에서 그 요구조건의 검수 판정 (PASS | FAIL | UNDETERMINED, 없으면 null)
    recorded_cost_usd: float | None  # 이 장면을 지난번에 만들 때의 비용 (기록이 있을 때)
    recorded_seconds: float | None  # 이 장면을 지난번에 만들 때 걸린 시간
    # 다시 만들 장면을 지금 실행하면 어떻게 만들어질지. 다시 만들지 않거나 생성기가 알려 주지 않으면 null
    production: PlannedProductionOut | None = None


class RevisionPlanEstimateOut(BaseModel):
    """다시 만들 장면의 어림 비용·시간의 합.

    장면마다 생성기가 알려 준 값(production)을 쓰고, 없으면 그 장면을 지난번에 만들 때의 기록을 쓴다.
    편집으로 만들 장면은 지난 기록으로 채우지 않는다 (새로 생성한 기록이라 더 크다).
    """

    regenerated_scene_count: int
    reused_scene_count: int
    estimated_scene_count: int  # 다시 만들 장면 가운데 어림값이 있는 장면 수
    edit_scene_count: int = 0  # 다시 만들 장면 가운데 부모 클립 편집으로 만들어질 장면 수
    cost_usd: float | None
    seconds: float | None


class RevisionPlanOut(BaseModel):
    """POST /api/versions/{version_id}/revision-plans — 수정 버전을 만들기 전의 계획 (상태는 바뀌지 않는다)."""

    version_id: str
    kind: str  # CHANGE | REPAIR
    interpreted: bool  # request_text 를 해석해서 만든 계획인가
    notes: str | None  # 요청을 어떻게 읽었는지 (해석했을 때)
    previous_value: str | None  # CHANGE: 이전 값 (장면마다 다르면 null)
    target_scene_keys: list[str]  # 다시 만들 장면
    scenes: list[RevisionPlanSceneOut]  # 모든 장면, 타임라인 순
    estimate: RevisionPlanEstimateOut
    # 이 계획대로 실행하려면 POST /api/versions/{version_id}/revisions 에 그대로 보낸다
    revision_request: CreateRevisionIn


class AcceptGenerationOut(BaseModel):
    version_id: str
    state: str


class WarningOut(BaseModel):
    scene_key: str
    start_ms: int
    end_ms: int
    message: str


class CheckResultOut(BaseModel):
    check_id: str
    check_type: str
    verdict: str
    score: float | None
    requirement_key: str | None
    entity_key: str | None
    scene_key: str | None
    warnings: list[WarningOut]
    detail: dict[str, Any]


class CheckSummaryOut(BaseModel):
    total: int
    passed: int
    failed: int
    undetermined: int
    requirement_reflection_rate: float | None
    consistency_rate: float | None
    preservation_rate: float | None


class ChecksOut(BaseModel):
    version_id: str
    state: str
    summary: CheckSummaryOut
    results: list[CheckResultOut]


class BoxOut(BaseModel):
    t_ms: int
    x: float
    y: float
    w: float
    h: float


class TrackSegmentOut(BaseModel):
    start_ms: int
    end_ms: int
    boxes: list[BoxOut]


class TrackRecordOut(BaseModel):
    scene_key: str
    entity_key: str
    status: str
    track_uri: str | None
    segments: list[TrackSegmentOut]


class EntityTracksOut(BaseModel):
    version_id: str
    entity_key: str
    records: list[TrackRecordOut]


# ---- US-04 전후 비교·대시보드 (ADR-0014) ----------------------------------------------


class VersionOverviewOut(BaseModel):
    """전후 비교의 양쪽과 대시보드의 각 버전이 함께 쓰는 버전 요약."""

    version_id: str
    parent_version_id: str | None
    state: str
    error_code: str | None
    error_message: str | None
    created_at: datetime
    video_url: str | None  # 결과 영상이 없으면(생성 전·실패) null
    video_media_type: str | None
    checks: CheckSummaryOut  # 검수 전이면 total=0, 지표 null
    revision: RevisionIntentOut | None = None  # 수정 버전이면 무엇을 하려는 수정인지 (ADR-0017)


class CheckDeltaOut(BaseModel):
    """검수 요약 지표의 차이 (after − before). 어느 한쪽이라도 값이 없으면 null."""

    requirement_reflection_rate: float | None
    consistency_rate: float | None
    preservation_rate: float | None


class ComparedSceneOut(BaseModel):
    """비교의 한쪽 장면. 그 버전에 장면이 없으면 SceneComparisonOut.before/after 자체가 null."""

    planned_start_ms: int
    planned_end_ms: int
    description: str
    clip_url: str | None  # 클립이 없으면(생성 전·처리 중·실패) null
    clip_media_type: str | None


class RequirementComparisonOut(BaseModel):
    change: str = Field(description="UNCHANGED | CHANGED | ADDED | REMOVED")
    entity_key: str | None  # null 이면 장면 수준 조건 (장소·조명·분위기·카메라)
    predicate: str
    value_kind: str | None
    relational: bool
    before_requirement_key: str | None  # 관계 상대가 바뀌면 양쪽 키가 다르다
    after_requirement_key: str | None
    before_value: str | None
    after_value: str | None
    before_verdict: str | None = Field(description="기준 버전의 REQUIREMENT 판정 (없으면 null)")
    after_verdict: str | None = Field(description="대상 버전의 REQUIREMENT 판정 (없으면 null)")


class SceneComparisonOut(BaseModel):
    scene_key: str
    status: str = Field(description="REUSED | REGENERATED | ADDED | REMOVED | UNAVAILABLE")
    conditions_changed: bool  # 장면 조건·등장 객체 정의가 바뀌었는가 (생성 전에도 계산)
    before: ComparedSceneOut | None
    after: ComparedSceneOut | None
    requirements: list[
        RequirementComparisonOut
    ]  # 그 장면의 모든 요구조건 (바뀐 것은 change 로 표시)


class ComparisonSummaryOut(BaseModel):
    scene_count: int
    status_counts: dict[str, int]  # 모든 장면 상태를 키로 둔다 (0 포함)
    changed_scene_keys: list[str]  # conditions_changed 인 장면
    requirement_change_count: int


class VersionComparisonOut(BaseModel):
    """GET /api/versions/{version_id}/comparison — 전후 비교 화면 한 장에 필요한 값 (US-04 AC-43)."""

    version_id: str  # 비교 대상 (after)
    against_version_id: str  # 비교 기준 (before, 생략하면 부모)
    against_is_parent: bool
    before: VersionOverviewOut
    after: VersionOverviewOut
    checks_delta: CheckDeltaOut
    summary: ComparisonSummaryOut
    scenes: list[SceneComparisonOut]  # 대상 버전의 장면 순서, 기준에만 있는 장면은 뒤에


class DashboardVersionOut(VersionOverviewOut):
    depth: int  # 원본 = 0
    child_version_ids: list[str]
    scene_count: int | None  # IR 이 없으면 null
    changed_scene_count: int | None  # 부모 대비 조건이 바뀐 장면 수 (원본·IR 없음이면 null)


class DashboardSummaryOut(BaseModel):
    version_count: int
    state_counts: dict[str, int]  # 모든 버전 상태를 키로 둔다 (0 포함)
    latest_version_id: str | None
    latest_completed_version_id: str | None


class ProjectDashboardOut(BaseModel):
    """GET /api/projects/{project_id}/dashboard — 버전 트리와 버전별 요약 (US-04 AC-41)."""

    project_id: str
    title: str
    created_at: datetime
    summary: DashboardSummaryOut
    versions: list[DashboardVersionOut]  # 생성 순. 트리는 parent_version_id / child_version_ids
