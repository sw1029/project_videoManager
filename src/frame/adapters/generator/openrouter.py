"""OpenRouter 영상 생성 API 어댑터 (FRAME_GENERATOR=external_api, ADR-0011).

흐름: 모델 메타데이터(GET /videos/models, 키 불필요) -> 제출(POST /videos) -> 폴링(GET /videos/{id})
-> 다운로드(GET /videos/{id}/content) -> ffmpeg 정규화(장면 길이·fps·해상도, 오디오 제거) -> ArtifactStore.put.
모델은 설정의 id 하나로 바꾼다 (기본 Wan 3.0). 허용 길이·화면비·해상도·seed 지원 여부는 메타데이터를 따른다.

비용 안전 규칙
- 제출 단계의 429·502 만 이 어댑터 안에서 다시 보낸다. 접수(202) 전이라 과금되지 않는다.
- 그 밖의 제출 오류, 접수 뒤 실패(failed·cancelled·expired), 시간 초과, 다운로드·정규화 실패는 retryable=False 로 보고해
  RetryingVideoGenerator 가 같은 장면을 다시 제출(재과금)하지 않게 한다.
- 폴링·다운로드(GET)의 일시 오류는 과금과 무관해 계속 시도한다.
- 범용 시간 제한(ADR-0012)으로 버려진 작업 스레드에서는 제출하지 않는다. 잠금·부모 주소·429 뒤 대기처럼 제출 전에
  기다리던 스레드가 마감 뒤에 깨어나 접수시키는 일을 막는다 (매 POST 직전에 확인한다).
- 키는 Authorization 헤더에만 싣고(리다이렉트로 넘기지 않음) 예외·로그·클립 메타데이터에 남기지 않는다.
  서명된 다운로드 URL(urls)도 키처럼 다뤄 기록하지 않는다.

부모 클립 편집 (ADR-0016, FRAME_VIDEO_EDIT_MODE)
- 수정 버전에서 다시 만드는 장면은 요청에 부모 클립이 실려 온다. 편집 모드를 켜면 장면을 통째로 다시 만드는 대신
  부모 클립을 고친다: previous_job(부모 작업을 previous_job_id 로 이어서 편집, 같은 모델·엔드포인트) 또는
  video_reference(부모 클립을 input_references 의 video_url 로 편집 모델에 보냄).
- 영상 참조는 HTTPS 주소만 받는다 (data URI 는 "Only HTTPS URLs are allowed" 로 거절된다, 2026-11-12 실호출).
  그래서 부모 클립을 만든 OpenRouter 작업의 저장본 서명 URL(urls)을 그 자리에서 받아 넘긴다. 이 주소는 요청 본문에만
  쓰고 기록하지 않는다. 저장본이 없거나 만료됐으면 편집하지 않고 새로 생성한다.
- 끝난 작업의 상태 응답에 저장본 주소가 빠져 올 때가 있다 (2026-11-12 실호출: 같은 작업을 10번 물으면 2~3번,
  끝난 지 몇 분 안 된 작업은 한동안 주소가 아예 없었다). 그래서 주소가 없으면
  FRAME_VIDEO_EDIT_PARENT_WAIT_S(기본 10초) 동안 다시 묻고, 그래도 없으면 새로 생성한다.

만들기 전 미리보기 (ADR-0020)
- preview(request) 는 그 장면이 지금 편집으로 만들어질지 새로 생성될지와 어림 비용·시간을 돌려준다. 제출하지 않는다.
- video_reference 편집이면 그때 부모 작업의 저장본 주소를 구해 **프로세스 메모리에만** 잠시 들고 있는다
  (기본 30분, 저장본 만료 1분 전까지). 변경 계획을 확인한 사용자가 실행하면 그 주소로 편집한다.
  서명 주소는 조회할 때마다 새로 서명돼 오고 작업이 만료될 때까지 유효하다 (2026-11-12 무료 조회로 확인).
  원장·메타데이터·로그에는 남기지 않는다.
- 시간 어림은 기록이 있을 때만 준다: 그 장면의 부모 클립을 같은 모델로 만들 때 걸린 시간, 없으면 이 프로세스가
  그 모델로 만든 작업들의 중앙값.
- 편집 요청이 접수 전에 거절되면(400·404·413·415·422) 과금 없이 지금처럼 새로 생성한다. 제출하기 전에 편집 모델이
  이 요청을 받을 수 없다고 드러나도(모델 목록에 없음, 장면이 그 모델의 최대 길이보다 김) 마찬가지다.
- 편집 결과는 부모 클립에 달려 있으므로 캐시·원장 키(result_key)에 부모 클립을 넣는다.
- 편집하지 못한 사정이 일시적이면(저장본 주소가 아직 나오지 않음) 새로 생성한 결과를 편집 요청의 키에 남기지 않는다
  (ADR-0021). 조건 키로만 남기고 편집 키는 released 로 비운다. 클립에는 provisional 표시를 해
  생성 캐시도 남기지 않게 한다.
  다음에 같은 요청이 오면 편집을 다시 시도하고, 그때도 못 하면 조건 키의 결과를 다시 쓴다 (또 생성하지 않는다).
  접수된 작업은 지금까지처럼 편집 키로 이어 받는다 (끊겨도 두 번 내지 않는다).

작업 원장 (ADR-0013)
- 접수(202) 직후, 폴링 전에 작업 id 를 원장(ArtifactStore 의 ledger/openrouter/<키>.json)에 남긴다.
  키는 fingerprint + 생성기 프로필(주소·모델·해상도·화면비·fps)이다.
- 같은 키로 다시 요청되면(재시작 뒤 복구, 시간 초과 뒤 재생성 등) 새로 제출하지 않는다. 진행 중이거나 결과를 아직
  못 받은 작업은 이어서 폴링·다운로드하고, 클립까지 저장한 작업은 그 클립을 그대로 돌려준다.
- 제공자 쪽에서 실패·취소·만료됐거나 작업이 없거나(404·410) 결과가 영상이 아니면 그 항목은 이어 받지 않는다
  (다음 요청이 새로 제출한다).
- 기한(ledger_ttl_seconds, 기본 24시간)은 제공자에게 다시 물어야 하는 항목에만 있다: 결과를 아직 못 받았거나,
  받았지만 클립이 저장소에 없는 항목. 제출 뒤 기한이 지나면 이어 받지 않는다. 클립이 남아 있는 완료 항목은
  기한 없이 그 클립을 돌려준다 (생성 캐시에 없는 결과도 여기에만 남아 있을 수 있다, ADR-0016·0021).
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote, urlsplit

from frame.adapters.shared import api_keys
from frame.adapters.shared.http import (
    HttpResponse,
    HttpTransport,
    TransportError,
    UrllibTransport,
)
from frame.adapters.shared.media.ffmpeg import resolve_ffmpeg
from frame.application.errors import (
    ConfigurationError,
    GenerationError,
    GenerationTimeout,
    StorageError,
)
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.generator import (
    GeneratedClip,
    ProductionMethod,
    ProductionPreview,
    RevisionDirective,
    SceneGenerationRequest,
    VideoGenerator,
)
from frame.application.ports.structurer import ReferenceImageInput
from frame.domain.enums import KeepAspect, RevisionKind
from frame.domain.hashing import canonical_hash
from frame.domain.ir import SceneSpec
from frame.domain.revision import KeepItem
from frame.domain.services.comparison import RequirementChangeKind, compare_requirements

from .abandonment import stop_if_abandoned
from .cache import KeyedLocks, generation_key
from .ffmpeg import FfmpegVideoNormalizer, NormalizedVideo, VideoNormalizer
from .ledger import (
    COMPLETED,
    FAILED,
    RELEASED,
    SUBMITTED,
    JobLedger,
    JobRecord,
    build_job_ledger,
)

log = logging.getLogger(__name__)

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_VIDEO_MODEL = "alibaba/wan-3.0"
KEY_ENV = "OPENROUTER_API_KEY"
# 제출 뒤 이 시간이 지나면 제공자에게 다시 물어야 하는 원장 항목(결과 미회수·클립 없음)은 이어 받지 않는다
LEDGER_TTL_SECONDS = 24 * 3600.0

_KEY_TOKEN = re.compile(r"sk-or-[A-Za-z0-9_\-]+")
_URL = re.compile(r"(https?)://([^/\s\"'<>?]+)[^\s\"'<>]*")
# 스킴 없이 온 주소 조각의 질의 문자열 (서명·만료 같은 값이 들어 있다)
_QUERY = re.compile(r"\?[\w.%~-]+=[^\s\"'<>]*")
# data URI 의 본문 (올려 보낸 참조 이미지 전체가 base64 로 들어 있다)
_DATA_URI = re.compile(r"(data:[\w.+/-]+;base64,)[A-Za-z0-9+/=]+")
_RETRY_SUBMIT = frozenset({429, 502})  # 접수 전 거절: 과금 없음
_TRANSIENT_GET = frozenset({408, 409, 425, 429})  # 조회·내려받기(GET)에서 곧 나아질 수 있는 4xx
_FAILED_STATES = frozenset({"failed", "cancelled", "expired"})
_GONE = frozenset({404, 410})  # 작업·결과가 없다 (만료 등): 원장 항목을 이어 받지 않는다
_MAX_SEED = 2**31  # 제공자 대부분(Wan 등)이 0 ~ 2^31-1 범위의 seed 를 받는다
EDIT_OFF = "off"
EDIT_PREVIOUS_JOB = "previous_job"  # 부모 클립을 만든 작업을 previous_job_id 로 이어서 편집
EDIT_VIDEO_REFERENCE = "video_reference"  # 부모 클립을 영상 참조(video_url)로 편집 모델에 보냄
EDIT_MODES = (EDIT_OFF, EDIT_PREVIOUS_JOB, EDIT_VIDEO_REFERENCE)
# 편집 요청이 이 상태로 거절되면(접수 전, 과금 없음) 새로 생성한다: 요청 형식·모델 기능 문제
_EDIT_FALLBACK = frozenset({400, 404, 413, 415, 422})
_JOB_ID = re.compile(r"^gen-vid-\d+-[0-9A-Za-z]{20}$")  # previous_job_id 가 받는 형식
_SHORT_SIDE = {
    "360p": 360,
    "480p": 480,
    "720p": 720,
    "768p": 768,
    "1080p": 1080,
    "1K": 1080,
    "2K": 1440,
    "4K": 2160,
}
_STATUS_HINTS = {
    400: "요청 형식 오류",
    401: "인증 실패, OpenRouter 키를 확인하세요",
    402: "크레딧 부족, OpenRouter 크레딧을 충전하세요",
    403: "권한 없음, 키 사용 제한이나 가드레일을 확인하세요",
    404: "모델 또는 작업을 찾을 수 없음",
    408: "요청 시간 초과",
    409: "결과가 아직 준비되지 않음",
    413: "요청 크기 초과, 참조 이미지가 너무 큼",
    429: "요청 한도 초과",
}


class _JobUnrecoverable(GenerationError):
    """이 작업은 더 이어 받을 수 없다: 제공자 쪽 실패·취소·만료, 작업 없음(404·410), 결과가 영상이 아님.

    원장 항목을 failed 로 바꿔 다음 요청이 새로 제출하게 한다. 이번 요청은 다시 제출하지 않는다 (retryable=False).
    """

    def __init__(self, *args: object) -> None:
        super().__init__(*args, retryable=False)


class _SubmitRejected(GenerationError):
    """제출이 HTTP 오류 응답으로 거절됐다 (접수 전이라 과금되지 않는다). status 로 원인을 가른다."""

    def __init__(self, message: str, *, status: int, retryable: bool) -> None:
        super().__init__(message, retryable=retryable)
        self.status = status


@dataclass(frozen=True)
class _EditPlan:
    mode: str
    model: str
    prompt: str
    parent_version_id: str
    parent_clip_uri: str
    previous_job_id: str | None = None  # previous_job: 이어 갈 작업
    parent_job_id: str | None = None  # video_reference: 저장본 주소를 받아 올 부모 작업


class _ParentUrl(NamedTuple):
    """영상 참조로 보낼 부모 클립 주소와, 그것을 얻으려고 부모 작업을 조회한 횟수 (주소가 None 이면 못 얻었다)."""

    url: str | None
    lookups: int
    held: bool = False  # 앞서 구해 들고 있던 주소인가 (미리보기 때 받아 둔 것 등)
    may_appear: bool = (
        False  # 주소가 없을 때: 나중에는 나올 수 있는가 (끝난 작업인데 아직 주소가 없음)
    )


class _HeldUrl(NamedTuple):
    """프로세스 메모리에만 들고 있는 부모 클립 주소. until(monotonic) 까지만 쓴다."""

    url: str
    until: float


# 부모 작업의 저장본 주소를 다시 물을 때: 처음 몇 번은 촘촘히 묻고(응답마다 주소가 빠지는 것을 넘긴다),
# 그 뒤로는 폴링 간격으로 묻는다
_PARENT_URL_QUICK_SECONDS = 2.0
_PARENT_URL_QUICK_LOOKUPS = 6
# 들고 있는 주소는 저장본이 만료되기 이만큼 전까지만 쓴다 (편집 모델이 내려받을 시간)
_HELD_URL_MARGIN_SECONDS = 60.0
# 시간 어림에 쓰는, 모델별로 기억하는 최근 작업 수
_SEEN_SECONDS_KEPT = 20
# 미리보기의 조회 하나가 기다리는 시간 상한 (변경 계획 응답이 위쪽 서버 때문에 오래 멈추지 않게)
_PREVIEW_REQUEST_TIMEOUT_SECONDS = 10.0


# ---- 키 -------------------------------------------------------------------------------


def resolve_openrouter_key(
    explicit: str | None = None,
    env: Mapping[str, str] | None = None,
    key_file: str | Path | None = None,
) -> str:
    """명시 인자 -> OPENROUTER_API_KEY -> 키 파일 순서. 못 찾으면 ConfigurationError (메시지에 키 내용 없음).

    키 파일에서는 'sk-or-' 로 시작하는 값만 받는다. 다른 회사 키 파일(F:/key.txt 의 OpenAI 키 등)을 잘못 지정해도
    그 키를 OpenRouter 로 보내지 않는다.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    source = os.environ if env is None else env
    from_env = (source.get(KEY_ENV) or "").strip()
    if from_env:
        return from_env
    if key_file:
        path = Path(key_file)
        if not path.is_file():
            raise ConfigurationError(f"OpenRouter 키 파일이 없습니다: {path}")
        text = path.read_text(encoding="utf-8", errors="ignore")
        found = api_keys.find_key(text, name=KEY_ENV, token=_KEY_TOKEN)
        if found is not None and found.startswith("sk-or-"):
            return found
        raise ConfigurationError(f"키 파일에서 OpenRouter 키(sk-or-...)를 찾지 못했습니다: {path}")
    raise ConfigurationError(
        f"OpenRouter API 키가 없습니다. {KEY_ENV} 환경 변수 또는 FRAME_VIDEO_KEY_FILE 을 설정하세요"
    )


def mask_key(key: str) -> str:
    """진단용 표시. 끝 4자리만 (ADR-0010). 짧은 키는 아무것도 보이지 않는다."""
    return api_keys.mask_key(key)


# ---- 모델 메타데이터 ---------------------------------------------------------------------


@dataclass(frozen=True)
class VideoModelInfo:
    """GET /api/v1/videos/models 의 항목 중 이 어댑터가 쓰는 필드."""

    id: str
    durations: tuple[int, ...] = ()
    aspect_ratios: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ()
    seed: bool = False
    audio: bool = False  # 오디오를 만들 수 있는 모델인가 (그렇다면 generate_audio=false 로 끈다)
    pricing: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> VideoModelInfo:
        return cls(
            id=str(data["id"]),
            durations=tuple(sorted(int(d) for d in data.get("supported_durations") or ())),
            aspect_ratios=tuple(str(a) for a in data.get("supported_aspect_ratios") or ()),
            resolutions=tuple(str(r) for r in data.get("supported_resolutions") or ()),
            seed=bool(data.get("seed")),
            audio=bool(data.get("generate_audio")),
            pricing={str(k): str(v) for k, v in (data.get("pricing_skus") or {}).items()},
        )

    def pick_duration(self, duration_ms: int) -> int:
        """장면 길이 이상인 가장 짧은 허용 길이(정수 초). 남는 부분은 정규화에서 잘라 낸다."""
        if not self.durations:
            return max(1, math.ceil(duration_ms / 1000))
        for seconds in self.durations:
            if seconds * 1000 >= duration_ms:
                return seconds
        raise GenerationError(
            f"장면 길이 {duration_ms} ms 가 모델 {self.id} 의 최대 길이 {self.durations[-1]}초를 넘습니다. "
            "장면을 나누세요",
            retryable=False,
        )

    def price_per_second(self, resolution: str) -> float | None:
        for sku in (f"duration_seconds_{resolution}", "duration_seconds"):
            raw = self.pricing.get(sku)
            if raw is not None:
                try:
                    return float(raw)
                except ValueError:
                    return None
        # 초당 센트로 적는 모델 (편집·업스케일 모델 등)
        for sku in (
            f"cents_per_second_output_{resolution}",
            "cents_per_second_output",
            f"cents_per_video_output_second_{resolution}",
        ):
            raw = self.pricing.get(sku)
            if raw is not None:
                try:
                    return float(raw) / 100
                except ValueError:
                    return None
        return None  # 토큰 단가 등 초 단가가 없는 모델


# ---- 프롬프트 ------------------------------------------------------------------------


def compile_video_prompt(
    request: SceneGenerationRequest,
    reference_numbers: Mapping[str | None, Sequence[int]] | None = None,
) -> str:
    """IR 장면 -> 영어 틀의 프롬프트. 사용자가 쓴 설명(한국어 등)은 번역하지 않고 그대로 넣는다.

    번역을 하지 않는 이유: 추가 호출·비용 없이 결정적이고(같은 IR -> 같은 프롬프트),
    문구(TEXT)·브랜드 철자를 보존한다.
    술어·값·객체 종류는 이미 영어 어휘라 틀만 영어로 짜도 모델이 조건을 구분해 읽는다.
    장면 수준 조건(분위기·조명 등)도 넣는다 (기본 compile_prompt 는 빠뜨린다).
    reference_numbers: 객체 키(None 은 장면 전체) -> input_references 의 1부터 센 번호.
    객체에 참조 이미지가 있으면 '참조와 똑같이 보이게, 자세·행동·배경은 이 장면대로' 라고 한 줄 덧붙인다.
    참조 이미지는 프롬프트가 무엇을 지켜야 하는지 말해 줄 때 더 잘 따른다 (ADR-0015).
    """
    numbers = reference_numbers or {}
    scene = request.scene
    looks = {
        r.entity_key: r.description.strip()
        for r in request.identity_references
        if r.entity_key and r.description.strip()
    }
    lines: list[str] = []
    if scene.description.strip():
        lines.append(f"Scene: {scene.description.strip()}")
    subjects: list[str] = []
    for entity in request.entities:
        details = [entity.description.strip()] if entity.description.strip() else []
        if entity.entity_key in looks:  # 기준 이미지에서 읽은 외형 (글로도 같은 모습을 고정한다)
            details.append(f"looks like: {looks[entity.entity_key]}")
        for r in scene.requirements:
            if r.entity_key != entity.entity_key:
                continue
            if r.related_entity_key is not None:
                details.append(f"{_words(r.predicate)} {r.related_entity_key}")
            else:
                details.append(f"{_words(r.predicate)}: {r.expected_value}")
        label = str(entity.kind).lower()
        refs = numbers.get(entity.entity_key) or ()
        if refs:
            label += ", " + _reference_phrase(refs)
        line = f"- {entity.entity_key} ({label})"
        subjects.append(line + (": " + "; ".join(details) if details else ""))
    if subjects:
        lines.append("Subjects:")
        lines.extend(subjects)
    conditions = [
        f"{_words(r.predicate)}: {r.expected_value}"
        for r in scene.requirements
        if r.entity_key is None
    ]
    if conditions:
        lines.append("Scene conditions: " + "; ".join(conditions))
    shared = numbers.get(None) or ()
    if shared:
        lines.append(f"Overall look: follow {_reference_phrase(shared)}")
    if any(numbers.get(entity.entity_key) for entity in request.entities):
        lines.append(CONSISTENCY_LINE)
    if request.directive is not None and request.directive.enforce:
        lines.append(_must_line(request.directive))
    return "\n".join(lines)


def _must_line(directive: RevisionDirective) -> str:
    """수정 버전이 이 장면에서 꼭 맞춰야 하는 요구조건 한 줄 (ADR-0017).

    객체 설명이나 장면 설명에 수정 전 표현이 남아 조건과 어긋날 수 있다 (실호출: '빨간 머그컵' + color: teal).
    무엇을 고치려는 수정인지 맨 끝에 따로 적는다.
    """
    parts = []
    for r in directive.enforce:
        subject = r.entity_key or "scene"
        if r.related_entity_key is not None:
            parts.append(f"{subject} {_words(r.predicate)} {r.related_entity_key}")
        else:
            parts.append(f"{subject} {_words(r.predicate)}: {r.expected_value}")
    if directive.kind is RevisionKind.REPAIR:
        note = " (an earlier render got this wrong)"
    elif directive.previous_value:
        note = f" (changed from {directive.previous_value})"
    else:
        note = ""
    return f"Must show: {'; '.join(parts)}{note}."


# 참조 이미지가 있는 객체: 모습은 참조에서, 자세·행동·구도·배경은 이 장면의 글에서 가져오게 한다
CONSISTENCY_LINE = (
    "Consistency: each subject marked with an Image number is the same individual or object as in "
    "that reference image. Keep its face, hair, body, clothing, colors, shape and markings "
    "identical to the reference; take pose, action, framing and background from this scene, not "
    "from the reference."
)


# 편집 지시문의 마지막 줄: 바꾸라고 한 것 말고는 입력 영상 그대로 둔다
EDIT_KEEP_LINE = (
    "Keep everything else exactly as in the input video: the same people (face, hair, body, "
    "clothing), the same objects, background, composition, camera movement, lighting and timing."
)


# 그대로 둘 요소를 지시문에 적을 때 쓰는 말 (ADR-0017)
_KEEP_WORDS = {
    KeepAspect.APPEARANCE: "appearance",
    KeepAspect.ACTION: "action and pose",
    KeepAspect.SHAPE: "shape and size",
    KeepAspect.BACKGROUND: "the background",
    KeepAspect.LIGHTING: "the lighting",
    KeepAspect.COMPOSITION: "the framing and camera",
}


def _keep_phrase(keep: Sequence[KeepItem]) -> str:
    """'barista (action and pose, appearance); cup (shape and size); the lighting' 꼴. 객체별로 묶는다."""
    by_entity: dict[str, list[str]] = {}
    scene_level: list[str] = []
    for item in keep:
        if item.entity_key is None:
            scene_level.append(_KEEP_WORDS[item.aspect])
        else:
            by_entity.setdefault(item.entity_key, []).append(_KEEP_WORDS[item.aspect])
    parts = [f"{key} ({', '.join(words)})" for key, words in by_entity.items()]
    return "; ".join([*parts, *scene_level])


def compile_edit_prompt(request: SceneGenerationRequest) -> str | None:
    """부모 클립을 고칠 때의 지시문: 부모 쪽 장면 조건과 지금 조건의 차이만 적는다 (ADR-0016).

    부모 클립이 없거나 글로 옮길 차이가 없으면 None (편집할 것이 없다 -> 새로 생성).
    수정 지시(ADR-0017)가 있으면 반영한다: REPAIR 는 조건의 차이가 없으므로 지켜지지 않았던 요구조건을
    '... must be ...' 로 적고, 그대로 둘 요소가 있으면 'Do not change: ...' 한 줄을 더한다.
    """
    parent = request.parent
    if parent is None:
        return None
    scene, before = request.scene, parent.scene
    changes: list[str] = []
    for row in compare_requirements(before, scene):
        subject = row.entity_key or "the scene"
        name = _words(row.predicate)
        old, new = row.before, row.after
        if row.change is RequirementChangeKind.CHANGED and old is not None and new is not None:
            if row.is_relational:
                changes.append(
                    f"- {subject}: now {name} {new.related_entity_key} "
                    f"(was {name} {old.related_entity_key})"
                )
            else:
                changes.append(
                    f"- {subject}: {name} changes from {old.expected_value} to {new.expected_value}"
                )
        elif row.change is RequirementChangeKind.ADDED and new is not None:
            if row.is_relational:
                changes.append(f"- {subject}: now {name} {new.related_entity_key}")
            else:
                changes.append(f"- {subject}: {name} is now {new.expected_value}")
        elif row.change is RequirementChangeKind.REMOVED and old is not None:
            if row.is_relational:
                changes.append(f"- {subject}: no longer {name} {old.related_entity_key}")
            else:
                changes.append(f"- {subject}: {name} ({old.expected_value}) is no longer required")
    described = {e.entity_key: e.description for e in parent.entities}
    for entity in request.entities:
        text = entity.description.strip()
        if text and entity.entity_key in described and described[entity.entity_key] != text:
            changes.append(f"- {entity.entity_key} is now: {text}")
    if scene.description.strip() and scene.description != before.description:
        changes.append(f"- the scene is now: {scene.description.strip()}")
    directive = request.directive
    if directive is not None and directive.kind is RevisionKind.REPAIR:
        for r in directive.enforce:
            subject = r.entity_key or "the scene"
            if r.related_entity_key is not None:
                changes.append(
                    f"- {subject}: must be shown as {_words(r.predicate)} {r.related_entity_key}"
                )
            else:
                changes.append(f"- {subject}: {_words(r.predicate)} must be {r.expected_value}")
    if not changes:
        return None
    lines = ["Edit the input video. Apply only these changes:", *changes]
    if directive is not None and directive.keep:
        lines.append(f"Do not change: {_keep_phrase(directive.keep)}.")
    lines.append(EDIT_KEEP_LINE)
    return "\n".join(lines)


def _words(predicate: str) -> str:
    return predicate.replace("_", " ")


def _reference_phrase(numbers: Sequence[int]) -> str:
    """input_references 의 순서 번호로 참조 이미지를 가리킨다: 'Image 1', 'Image 1 and Image 2'.

    Wan 의 참조 영상 생성 문서가 정한 지칭이다 (첫 글자 대문자, 낱말과 숫자 사이 한 칸, 배열 순서와 같은 번호).
    다른 모델도 자연어로 읽을 수 있는 표현이다.
    """
    return " and ".join(f"Image {n}" for n in numbers)


# ---- 출력 규격 -----------------------------------------------------------------------


def output_size(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    """정규화할 클립 크기. 짧은 변 = 해상도 표기(480p -> 480), 긴 변은 화면비로 계산해 짝수로 맞춘다.

    예) 9:16 480p -> 480x854, 9:16 720p -> 720x1280
    """
    short = _SHORT_SIDE.get(resolution)
    if short is None:
        raise ConfigurationError(
            f"지원하지 않는 해상도 표기입니다: {resolution} (사용 가능: {', '.join(_SHORT_SIDE)})"
        )
    try:
        w, h = (int(part) for part in aspect_ratio.split(":"))
    except ValueError as exc:
        raise ConfigurationError(
            f"화면비 형식이 잘못되었습니다: {aspect_ratio} (예: 9:16)"
        ) from exc
    if w <= 0 or h <= 0:
        raise ConfigurationError(f"화면비 형식이 잘못되었습니다: {aspect_ratio} (예: 9:16)")
    if w < h:
        return short, _even(short * h / w)
    if w > h:
        return _even(short * w / h), short
    return short, short


def _even(value: float) -> int:
    return int(round(value / 2) * 2)


def _checked_base_url(base_url: str) -> str:
    url = base_url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.netloc:
        return url
    if parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}:
        return url  # 로컬 시험 서버
    raise ConfigurationError(
        f"OpenRouter 주소는 https 여야 합니다 (키를 평문으로 보내지 않도록): {base_url}"
    )


# ---- 생성기 --------------------------------------------------------------------------


class OpenRouterVideoGenerator(VideoGenerator):
    """OpenRouter /api/v1/videos 로 장면 하나를 만들고, 장면 길이에 맞춘 무음 mp4 를 저장한다."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        api_key: str,
        model: str = DEFAULT_VIDEO_MODEL,
        resolution: str = "720p",
        aspect_ratio: str = "9:16",
        fps: int = 30,
        base_url: str = OPENROUTER_API_BASE,
        timeout_seconds: float = 600.0,
        poll_interval_seconds: float = 20.0,
        submit_attempts: int = 3,
        download_attempts: int = 3,
        request_timeout_seconds: float = 60.0,
        transport: HttpTransport | None = None,
        normalizer: VideoNormalizer | None = None,
        ledger: JobLedger | None = None,
        ledger_ttl_seconds: float = LEDGER_TTL_SECONDS,
        edit_mode: str = EDIT_OFF,
        edit_model: str | None = None,
        edit_parent_wait_seconds: float = 10.0,
        edit_preview_wait_seconds: float = 3.0,
        parent_url_hold_seconds: float = 1800.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ConfigurationError("OpenRouter API 키가 비어 있습니다")
        if not _fits_in_a_header(api_key.strip()):
            # 이런 키로 요청하면 프로토콜 오류가 나고, 그 오류의 문구에는 키가 통째로 들어 있다
            raise ConfigurationError(
                "OpenRouter API 키에 쓸 수 없는 글자가 있습니다 (공백·줄바꿈·영문 밖의 글자). "
                "키 파일이나 환경 변수에 키만 들어 있는지 확인하세요"
            )
        if fps <= 0 or timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ConfigurationError("fps·시간 제한·폴링 간격은 0 보다 커야 합니다")
        if edit_mode not in EDIT_MODES:
            raise ConfigurationError(
                f"FRAME_VIDEO_EDIT_MODE 값이 잘못되었습니다: {edit_mode} "
                f"(사용 가능: {', '.join(EDIT_MODES)})"
            )
        if edit_parent_wait_seconds < 0:
            raise ConfigurationError(
                "FRAME_VIDEO_EDIT_PARENT_WAIT_S 는 0 이상이어야 합니다 (0 이면 한 번만 묻는다)"
            )
        if edit_preview_wait_seconds < 0 or parent_url_hold_seconds < 0:
            raise ConfigurationError(
                "미리보기에서 부모 주소를 다시 묻는 시간과 주소를 들고 있는 시간은 0 이상이어야 합니다"
            )
        # 부모 클립 편집 (ADR-0016). edit_model 은 video_reference 에서만 쓰고, 비우면 생성 모델을 쓴다
        self.edit_mode = edit_mode
        self.edit_model = (edit_model or "").strip() or None
        # 부모 작업의 저장본 주소가 상태 응답에 없을 때 다시 묻는 시간
        self.edit_parent_wait_seconds = float(edit_parent_wait_seconds)
        # 미리보기(ADR-0020): 계획 응답이 늦지 않게 짧게만 다시 묻고, 받은 주소는 메모리에 잠시 들고 있는다
        self.edit_preview_wait_seconds = float(edit_preview_wait_seconds)
        self.parent_url_hold_seconds = float(parent_url_hold_seconds)
        self._held_urls: dict[str, _HeldUrl] = {}
        self._seen_seconds: dict[str, deque[float]] = {}
        self._memo_lock = threading.Lock()
        self._api_key = api_key.strip()
        self.key_hint = mask_key(self._api_key)
        self.base_url = _checked_base_url(base_url)
        self.model = model
        self.resolution = resolution
        self.aspect_ratio = aspect_ratio
        self.fps = int(fps)
        self.output_size = output_size(resolution, aspect_ratio)
        self.timeout_seconds = float(timeout_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.submit_attempts = max(1, int(submit_attempts))
        self.download_attempts = max(1, int(download_attempts))
        self.request_timeout_seconds = float(request_timeout_seconds)
        self._store = store
        self._http = transport or UrllibTransport()
        self._normalizer = normalizer or FfmpegVideoNormalizer(resolve_ffmpeg())
        self._sleep = sleep
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._model_infos: dict[str, VideoModelInfo] = {}
        self._ledger = ledger if ledger is not None else build_job_ledger(store, "openrouter")
        self.ledger_ttl_seconds = float(ledger_ttl_seconds)
        self._locks = KeyedLocks()

    # ---- 포트 구현 ----
    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip:
        key = generation_key(self.result_key(request), self.profile())
        with self._locks.hold(key):  # 같은 장면의 동시 요청이 두 번 제출(과금)하지 않게 한다
            record = self._resumable(key)
            if record is None:
                return self._generate_new(request, key)
            if self._has_clip(record):
                return self._reuse(request, record)
            return self._collect(request, record, resumed=True)

    def _generate_new(self, request: SceneGenerationRequest, key: str) -> GeneratedClip:
        """새 작업을 제출한다.

        부모 클립을 편집할 수 있으면 편집 요청을 먼저 보내고, 접수 전에 거절되면 새로 생성한다 (ADR-0016).
        """
        rejected: dict[str, Any] | None = None
        plan = self._edit_plan(request)
        if plan is not None:
            edited = self._edit(request, key, plan)
            if isinstance(edited, GeneratedClip):
                return edited
            rejected = edited
            reused = self._reuse_regenerated(request, key, rejected)
            if reused is not None:
                return reused
        body, details = self._generation_body(request)
        if rejected is not None:
            details["edit"] = rejected
        return self._accepted(request, key, self._submit(body), details)

    def _edit(
        self, request: SceneGenerationRequest, key: str, plan: _EditPlan
    ) -> GeneratedClip | dict[str, Any]:
        """부모 클립 편집을 시도한다. 접수되면 그 결과 클립을, 접수 전에 막히면 그 사정을 적은 편집 기록을 돌려준다.

        접수 전에 막힌 편집은 과금되지 않았다. 부르는 쪽이 그 기록을 달고 장면을 새로 생성한다.
        """
        source = (
            self._parent_video_url(plan)
            if plan.mode == EDIT_VIDEO_REFERENCE
            else _ParentUrl(None, 0)
        )
        if plan.mode == EDIT_VIDEO_REFERENCE and source.url is None:
            # 부모 클립을 영상 참조로 넘길 주소가 없다 (저장본 없음·만료·아직 안 나옴): 제출하지 않고 새로 만든다
            log.warning(
                "부모 클립의 저장본 주소를 얻지 못해 편집하지 않고 새로 생성합니다 "
                "(부모 작업 %s, 장면 %s, 조회 %d회)",
                plan.parent_job_id,
                request.scene.scene_key,
                source.lookups,
            )
            rejected: dict[str, Any] = {
                "mode": plan.mode,
                "model": plan.model,
                "applied": False,
                "parent_version_id": plan.parent_version_id,
                "parent_clip_uri": plan.parent_clip_uri,
                "parent_url_lookups": source.lookups,
                "fallback_reason": (
                    f"부모 작업의 저장본이 없거나 만료됨 ({source.lookups}회 조회)"
                ),
            }
            if source.may_appear:
                # 일시적인 사정이다: 주소가 나온 뒤 같은 요청이 오면 편집할 수 있다 (ADR-0021)
                rejected["transient"] = True
                rejected["fallback_reason"] = (
                    f"부모 작업의 저장본 주소가 아직 나오지 않음 ({source.lookups}회 조회). "
                    "같은 수정을 다시 요청하면 편집을 다시 시도함"
                )
            return rejected
        attempt = self._edit_attempt(plan, source)
        try:
            body, details = self._edit_body(request, plan, source, attempt)
        except GenerationError as exc:
            if (
                exc.retryable
            ):  # 모델 목록을 받지 못한 것은 일시 오류다. 새로 생성하는 데도 같은 목록이 필요하다
                raise
            # 편집 모델이 이 요청을 받을 수 없다는 것이 제출 전에 드러났다. 과금 전이니 장면을 새로 만든다
            log.warning(
                "편집 모델(%s)이 장면 %s 를 받을 수 없어 편집하지 않고 새로 생성합니다: %s",
                plan.model,
                request.scene.scene_key,
                exc,
            )
            return {**attempt, "applied": False, "fallback_reason": str(exc)[:300]}
        try:
            job = self._submit(body)
        except _SubmitRejected as exc:
            if exc.status not in _EDIT_FALLBACK:
                raise
            # 접수 전 거절이라 과금되지 않았다. 편집을 포기하고 지금까지처럼 장면을 새로 만든다
            self._drop_held_url(plan.parent_job_id)  # 그 주소가 원인일 수 있다
            log.warning(
                "OpenRouter 가 부모 클립 편집 요청(%s, model=%s)을 거절해 새로 생성합니다: %s",
                plan.mode,
                plan.model,
                exc,
            )
            return {**attempt, "applied": False, "fallback_reason": str(exc)[:300]}
        try:
            return self._accepted(request, key, job, details)
        except GenerationError:
            # 편집 작업이 접수 뒤 실패했다. 넘긴 주소가 원인일 수 있으니 들고 있지 않는다
            self._drop_held_url(plan.parent_job_id)
            raise

    def _reuse_regenerated(
        self, request: SceneGenerationRequest, key: str, rejected: dict[str, Any]
    ) -> GeneratedClip | None:
        """편집이 거절된 요청: 같은 조건으로 이미 새로 생성해 둔 결과가 원장에 있으면 다시 제출하지 않고 그것을 쓴다.

        편집할 요청의 키(부모 클립 포함)와 새로 생성할 때의 키(조건 + 정체성 기준)가 달라서, 이 확인이 없으면
        편집 모드만 바꿔 같은 수정을 다시 돌릴 때 같은 장면을 또 생성(과금)한다. 편집을 끈 설정이 만든 결과와,
        다른 편집 설정이 거절된 뒤 새로 만든 결과(`_share_regenerated`)가 이 키로 남아 있다.

        편집하지 못한 사정이 일시적이면 편집 요청의 키에는 남기지 않는다 (ADR-0021): 다음 요청이 편집을 다시 시도한다.
        """
        plain_key = generation_key(request.generation_fingerprint, self.profile())
        record = self._resumable(plain_key)
        if record is None or record.status != COMPLETED:
            return None
        if not self._has_clip(record):
            if rejected.get("transient") is not True:
                return None
            return self._redownload_provisional(request, plain_key, rejected)
        found = replace(
            record,
            key=key,
            clip_metadata={**record.clip_metadata, "edit": rejected},
            updated_at=self._wall_clock(),
        )
        if rejected.get("transient") is True:
            found = replace(found, clip_metadata={**found.clip_metadata, "provisional": True})
        else:
            self._remember(found)  # 다음에는 편집 키로 바로 찾는다
        return self._reuse(request, found)

    def _redownload_provisional(
        self, request: SceneGenerationRequest, plain_key: str, rejected: dict[str, Any]
    ) -> GeneratedClip | None:
        """조건 키에 남긴 임시 결과의 클립 파일이 없다: 다시 생성(과금)하지 않고 끝난 작업의 결과를 다시 내려받는다.

        편집 요청의 키를 비우기 전에는 그 키의 완료 기록으로 이렇게 했다 (ADR-0013).
        비운 뒤에는 조건 키의 기록으로 한다.
        """
        with self._locks.hold(plain_key):  # 같은 조건을 편집 없이 만드는 요청과 겹치지 않게
            record = self._resumable(plain_key)
            if record is None or record.status != COMPLETED:
                return None
            clip = self._collect(request, record, resumed=True)
        return replace(clip, metadata={**clip.metadata, "edit": rejected, "provisional": True})

    def _generation_body(
        self, request: SceneGenerationRequest
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        info = self.model_info()
        self._check_supported(info)
        scene = request.scene
        seconds, estimated_cost = self._generation_billing(info, scene)
        # 사용자 참조 다음에 정체성 기준 이미지를 보낸다 (ADR-0015). 같은 이미지는 한 번만 보낸다
        references, numbers = self._input_references(
            (*request.references, *request.identity_references)
        )
        prompt = compile_video_prompt(request, numbers)
        seed = request.seed % _MAX_SEED if info.seed and request.seed is not None else None

        body: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "duration": seconds,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "generate_audio": False,
        }
        if seed is not None:
            body["seed"] = seed
        if references:
            # frame_images 와 함께 보내면 참조가 무시되고 image-to-video 로 처리되므로 frame_images 는 쓰지 않는다
            body["input_references"] = references
        details: dict[str, Any] = {
            "model": self.model,
            "estimated_cost_usd": estimated_cost,
            "billed_seconds": seconds,
            "seed": seed,
            "reference_count": len(references),
            "identity_reference_count": len(request.identity_references),
            "prompt": prompt,
        }
        return body, details

    def _accepted(
        self,
        request: SceneGenerationRequest,
        key: str,
        job: Mapping[str, Any],
        details: dict[str, Any],
    ) -> GeneratedClip:
        """접수(202)된 작업을 원장에 남기고 결과를 기다린다."""
        job_id = job.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise GenerationError(
                "OpenRouter 가 작업을 받았지만 작업 id 가 없습니다. 활동 기록(Activity)을 확인하세요",
                retryable=False,
            )
        scene = request.scene
        record = JobRecord(
            key=key,
            job_id=job_id,
            status=SUBMITTED,
            submitted_at=self._wall_clock(),
            version_id=request.version_id,
            scene_key=scene.scene_key,
            profile=self.profile(),
            details={"generation_id": job.get("generation_id"), **details},
        )
        # 폴링 전에 남긴다: 이 뒤에 프로세스가 죽어도 다음 인스턴스가 같은 작업을 이어 받는다
        self._remember(record)
        edit = details.get("edit") or {}
        log.info(
            "OpenRouter 영상 작업 접수: job=%s model=%s duration=%ss resolution=%s scene=%s%s",
            job_id,
            details.get("model"),
            details.get("billed_seconds"),
            self.resolution,
            scene.scene_key,
            f" (부모 클립 편집: {edit.get('mode')})" if edit.get("applied") else "",
        )
        return self._collect(request, record, resumed=False)

    # ---- 부모 클립 편집 (ADR-0016) ----
    def result_key(self, request: SceneGenerationRequest) -> str:
        """부모 클립을 편집해서 만들 요청은 결과가 부모 클립에도 달려 있다. 그때만 키에 부모를 넣는다."""
        plan = self._edit_plan(request)
        if plan is None:
            return request.generation_fingerprint
        return canonical_hash(
            {
                "base": request.generation_fingerprint,
                "edit": {
                    "mode": plan.mode,
                    "model": plan.model,
                    "parent": plan.previous_job_id or plan.parent_job_id or plan.parent_clip_uri,
                },
            }
        )

    def _edit_plan(self, request: SceneGenerationRequest) -> _EditPlan | None:
        """이 요청을 부모 클립 편집으로 만들 수 있으면 그 계획, 아니면 None (새로 생성)."""
        parent = request.parent
        if self.edit_mode == EDIT_OFF or parent is None:
            return None
        scene = request.scene
        # 길이나 등장 객체가 달라진 장면은 편집으로 만들 수 없다 (다시 생성한다)
        if parent.scene.duration_ms != scene.duration_ms:
            return None
        if set(parent.scene.entity_keys) != set(scene.entity_keys):
            return None
        prompt = compile_edit_prompt(request)
        if prompt is None:
            return None
        clip = parent.clip
        if self.edit_mode == EDIT_PREVIOUS_JOB:
            # 같은 모델·엔드포인트가 만든 완료 작업만 이어 갈 수 있다
            job_id = clip.metadata.get("job_id")
            same_model = clip.metadata.get("model") == self.model
            if not isinstance(job_id, str) or not _JOB_ID.match(job_id) or not same_model:
                return None
            return _EditPlan(
                EDIT_PREVIOUS_JOB, self.model, prompt, parent.version_id, clip.uri, job_id
            )
        # 영상 참조는 HTTPS 주소만 받는다: 클립이 이미 원격 주소이거나, 부모 작업의 저장본 주소를 받아 올 수 있어야 한다
        job_id = clip.metadata.get("job_id")
        parent_job = job_id if isinstance(job_id, str) and _JOB_ID.match(job_id) else None
        if parent_job is None and not self._usable_video_url(clip.uri):
            return None
        return _EditPlan(
            EDIT_VIDEO_REFERENCE,
            self.edit_model or self.model,
            prompt,
            parent.version_id,
            clip.uri,
            parent_job_id=parent_job,
        )

    def _usable_video_url(self, url: str) -> bool:
        """영상 참조로 보낼 수 있는 주소인가: https, 또는 로컬 시험 서버를 쓸 때의 로컬 http."""
        parts = urlsplit(url)
        if parts.scheme == "https" and parts.netloc:
            return True
        local = {"localhost", "127.0.0.1", "::1"}
        return (
            parts.scheme == "http"
            and parts.hostname in local
            and urlsplit(self.base_url).hostname in local
        )

    def _parent_video_url(
        self,
        plan: _EditPlan,
        *,
        wait_seconds: float | None = None,
        request_timeout: float | None = None,
    ) -> _ParentUrl:
        """부모 클립을 내려받을 수 있는 주소. 못 구하면 주소가 None (편집하지 않는다). 주소는 기록하지 않는다.

        끝난 작업의 상태 응답에 저장본 주소(urls)가 빠져 올 때가 있어, 끝났는데 주소가 없는 응답과 일시적인 조회
        실패는 edit_parent_wait_seconds(wait_seconds 를 주면 그 시간) 동안 다시 묻는다. 없어졌거나 끝나지 않은
        작업은 다시 묻지 않는다. 앞서 구해 들고 있는 주소가 있으면 묻지 않고 그것을 쓴다 (ADR-0020).
        """
        if self._usable_video_url(plan.parent_clip_uri):
            return _ParentUrl(plan.parent_clip_uri, 0)
        if plan.parent_job_id is None:
            return _ParentUrl(None, 0)
        held = self._held_url(plan.parent_job_id)
        if held is not None:
            return _ParentUrl(held, 0, held=True)
        wait = self.edit_parent_wait_seconds if wait_seconds is None else wait_seconds
        deadline = self._monotonic() + wait
        lookups = 0
        while True:
            lookups += 1
            url, ask_again, expires_at = self._lookup_parent_url(
                plan.parent_job_id, request_timeout
            )
            if url is not None:
                self._hold_url(plan.parent_job_id, url, expires_at)
                return _ParentUrl(url, lookups)
            if not ask_again:
                return _ParentUrl(None, lookups)
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return _ParentUrl(None, lookups, may_appear=True)
            delay = (
                _PARENT_URL_QUICK_SECONDS
                if lookups < _PARENT_URL_QUICK_LOOKUPS
                else self.poll_interval_seconds
            )
            self._sleep(min(delay, remaining))

    def _lookup_parent_url(
        self, job_id: str, timeout: float | None = None
    ) -> tuple[str | None, bool, float | None]:
        """부모 작업을 한 번 조회한다: (저장본 주소, 주소가 없을 때 다시 물어볼 만한가, 저장본 만료 시각)."""
        try:
            resp = self._http.request(
                "GET",
                self._job_url(job_id),
                headers=self._auth_headers(),
                timeout=timeout or self.request_timeout_seconds,
            )
        except TransportError:
            return None, True, None
        if resp.status != 200:
            return None, _may_pass(resp.status), None
        try:
            data = self._json(resp)
        except GenerationError:
            return None, True, None
        if data.get("status") != "completed":
            return None, False, None
        urls = data.get("urls")
        if not isinstance(urls, list) or not urls:
            # 끝났는데 주소가 없다: 이 응답에만 빠졌거나 아직 나오지 않았다
            return None, True, None
        url = urls[0]
        if isinstance(url, str) and self._usable_video_url(url):
            expires_at = data.get("expires_at")
            if isinstance(expires_at, int | float) and not isinstance(expires_at, bool):
                return url, False, float(expires_at)
            return url, False, None
        return None, False, None

    # ---- 부모 주소 보관 (ADR-0020): 프로세스 메모리에만, 잠시 ----
    def _hold_url(self, job_id: str, url: str, expires_at: float | None) -> None:
        hold = self.parent_url_hold_seconds
        if expires_at is not None:  # 저장본이 만료되기 조금 전까지만
            hold = min(hold, expires_at - self._wall_clock() - _HELD_URL_MARGIN_SECONDS)
        if hold <= 0:
            return
        now = self._monotonic()
        with self._memo_lock:
            stale = [key for key, item in self._held_urls.items() if item.until <= now]
            for key in stale:
                del self._held_urls[key]
            self._held_urls[job_id] = _HeldUrl(url, now + hold)

    def _drop_held_url(self, job_id: str | None) -> None:
        if job_id is not None:
            with self._memo_lock:
                self._held_urls.pop(job_id, None)

    def _held_url(self, job_id: str) -> str | None:
        with self._memo_lock:
            item = self._held_urls.get(job_id)
            if item is None:
                return None
            if item.until <= self._monotonic():
                del self._held_urls[job_id]
                return None
            return item.url

    # ---- 만들기 전 미리보기 (ADR-0020) ----
    def preview(self, request: SceneGenerationRequest) -> ProductionPreview:
        """이 장면을 지금 만들면 편집으로 만들지 새로 생성할지와 그 어림값. 아무것도 제출하지 않는다.

        video_reference 편집이면 부모 작업의 저장본 주소를 여기서 구해 들고 있는다. 계획을 확인한 사용자가 실행할 때
        상태 응답에서 주소가 빠져도 그 주소로 편집한다. 주소가 아직 없으면 새로 생성될 것이라고 사유와 함께 알린다.
        """
        scene = request.scene
        plan = self._edit_plan(request)
        quick = min(self.request_timeout_seconds, _PREVIEW_REQUEST_TIMEOUT_SECONDS)
        reason: str | None = None
        if plan is not None:
            if plan.mode == EDIT_VIDEO_REFERENCE:
                # 미리보기는 실행할 때보다 오래 기다리지 않는다
                wait = min(self.edit_preview_wait_seconds, self.edit_parent_wait_seconds)
                source = self._parent_video_url(plan, wait_seconds=wait, request_timeout=quick)
                if source.url is None:
                    reason = (
                        "부모 영상의 저장본 주소가 아직 나오지 않아 지금은 편집할 수 없습니다 "
                        f"({source.lookups}회 조회). 잠시 뒤 계획을 다시 확인하면 편집으로 만들 수 있습니다"
                        if source.may_appear
                        else "부모 영상의 저장본이 없거나 만료돼 편집할 수 없습니다"
                    )
            cost = None
            if reason is None:
                try:
                    info = self.model_info(plan.model, timeout=quick)
                    cost = self._edit_billing(info, scene)[2]
                except GenerationError as exc:
                    if exc.retryable:
                        log.warning("편집 모델의 가격을 읽지 못해 어림 비용을 비웁니다: %s", exc)
                    else:
                        # 실행하면 제출 전 확인에서 걸려 새로 생성된다. 계획도 그렇게 알린다
                        detail = str(exc)[:240]
                        reason = f"편집 모델({plan.model})이 이 장면을 받을 수 없습니다: {detail}"
            if reason is None:
                return ProductionPreview(
                    ProductionMethod.EDIT,
                    model=plan.model,
                    cost_usd=cost,
                    seconds=self._typical_seconds(plan.model, request),
                )
        cost = None
        try:
            cost = self._generation_billing(self.model_info(timeout=quick), scene)[1]
        except GenerationError as exc:
            log.warning("생성 모델의 가격·길이를 읽지 못해 어림 비용을 비웁니다: %s", exc)
        return ProductionPreview(
            ProductionMethod.GENERATE,
            model=self.model,
            cost_usd=cost,
            seconds=self._typical_seconds(self.model, request),
            edit_unavailable_reason=reason,
        )

    def _generation_billing(
        self, info: VideoModelInfo, scene: SceneSpec
    ) -> tuple[int, float | None]:
        """새로 생성할 때 (보낼 길이(초), 어림 비용)."""
        seconds = info.pick_duration(scene.duration_ms)
        price = info.price_per_second(self.resolution)
        return seconds, round(price * seconds, 6) if price is not None else None

    def _edit_billing(
        self, info: VideoModelInfo, scene: SceneSpec
    ) -> tuple[int | None, int, float | None]:
        """편집할 때 (보낼 길이(초, 모델이 받지 않으면 None), 과금되는 초, 어림 비용)."""
        seconds = info.pick_duration(scene.duration_ms) if info.durations else None
        billed = seconds if seconds is not None else max(1, math.ceil(scene.duration_ms / 1000))
        price = info.price_per_second(self.resolution)
        return seconds, billed, round(price * billed, 6) if price is not None else None

    def _typical_seconds(self, model: str, request: SceneGenerationRequest) -> float | None:
        """그 모델로 이 장면을 만드는 데 걸릴 시간의 어림. 기록이 없으면 None.

        그 장면의 부모 클립을 같은 모델로 만들 때 걸린 시간이 가장 가깝다. 없으면 이 프로세스가 그 모델로 만든
        작업들의 중앙값을 쓴다.
        """
        parent = request.parent
        if parent is not None and parent.clip.metadata.get("model") == model:
            recorded = parent.clip.metadata.get("generation_seconds")
            if isinstance(recorded, int | float) and not isinstance(recorded, bool):
                return float(recorded)
        with self._memo_lock:
            seen = list(self._seen_seconds.get(model, ()))
        return round(statistics.median(seen), 1) if seen else None

    def _note_seconds(self, model: str, seconds: float) -> None:
        with self._memo_lock:
            self._seen_seconds.setdefault(model, deque(maxlen=_SEEN_SECONDS_KEPT)).append(seconds)

    def _edit_attempt(self, plan: _EditPlan, source: _ParentUrl) -> dict[str, Any]:
        """클립 메타데이터에 남기는 편집 시도 기록 (applied 는 편집으로 만들었는가). 주소 자체는 남기지 않는다."""
        edit: dict[str, Any] = {
            "mode": plan.mode,
            "model": plan.model,
            "applied": True,
            "parent_version_id": plan.parent_version_id,
            "parent_clip_uri": plan.parent_clip_uri,
        }
        if plan.previous_job_id is not None:
            edit["previous_job_id"] = plan.previous_job_id
        if plan.parent_job_id is not None:
            edit["parent_job_id"] = plan.parent_job_id
        if source.lookups:
            # 저장본 주소를 얻기까지 부모 작업을 조회한 횟수
            edit["parent_url_lookups"] = source.lookups
        if source.held:
            # 앞서(변경 계획을 볼 때 등) 구해 들고 있던 주소를 썼다
            edit["parent_url_held"] = True
        return edit

    def _edit_body(
        self,
        request: SceneGenerationRequest,
        plan: _EditPlan,
        source: _ParentUrl,
        attempt: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """편집 요청 본문. 모델 메타데이터에 없는 선택 값은 보내지 않는다 (목록에 없는 값은 거부된다).

        source 는 영상 참조로 보낼 부모 클립 주소다 (video_reference 에서만 쓴다). attempt 는 편집 시도 기록이다.
        편집 모델이 목록에 없거나 장면이 그 모델의 최대 길이보다 길면 GenerationError(retryable=False) 다.
        """
        video_url = source.url
        info = self.model_info(plan.model)
        scene = request.scene
        seconds, billed, estimated_cost = self._edit_billing(info, scene)
        seed = request.seed % _MAX_SEED if info.seed and request.seed is not None else None
        body: dict[str, Any] = {"model": plan.model, "prompt": plan.prompt}
        if seconds is not None:
            body["duration"] = seconds
        if self.aspect_ratio in info.aspect_ratios:
            body["aspect_ratio"] = self.aspect_ratio
        if self.resolution in info.resolutions:
            body["resolution"] = self.resolution
        if info.audio:
            body["generate_audio"] = False
        if seed is not None:
            body["seed"] = seed
        if plan.mode == EDIT_PREVIOUS_JOB:
            body["previous_job_id"] = plan.previous_job_id
        else:
            body["input_references"] = [{"type": "video_url", "video_url": {"url": video_url}}]
        details: dict[str, Any] = {
            "model": plan.model,
            "estimated_cost_usd": estimated_cost,
            "billed_seconds": billed,
            "seed": seed,
            "reference_count": 0 if plan.mode == EDIT_PREVIOUS_JOB else 1,
            "identity_reference_count": 0,
            "prompt": plan.prompt,
            "edit": dict(attempt),
        }
        return body, details

    def _collect(
        self, request: SceneGenerationRequest, record: JobRecord, *, resumed: bool
    ) -> GeneratedClip:
        """접수된 작업을 기다려 받고 정규화해 저장한다. resumed 면 원장에서 이어 받은 작업이다."""
        job_id = record.job_id
        scene = request.scene
        if resumed:
            log.info(
                "OpenRouter 영상 작업 이어 받기 (새로 제출하지 않음): job=%s 원장 상태=%s "
                "처음 버전=%s 이번 버전=%s scene=%s",
                job_id,
                record.status,
                record.version_id,
                request.version_id,
                scene.scene_key,
            )
        started = self._monotonic()
        try:
            final, polls = self._wait(job_id, started, poll_now=resumed)
            generation_seconds = self._monotonic() - started
            raw = self._download(job_id)
        except _JobUnrecoverable as exc:
            self._remember(
                replace(record, status=FAILED, error=str(exc), updated_at=self._wall_clock())
            )
            raise
        normalized = self._normalize(request, raw, job_id)
        uri = self._store.put(
            f"clips/{request.version_id}/{scene.scene_key}.mp4", normalized.data, "video/mp4"
        )

        raw_usage = final.get("usage")
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        details = record.details
        metadata: dict[str, object] = {
            "generator": "openrouter",
            "model": details.get("model") or self.model,
            "job_id": job_id,
            "generation_id": final.get("generation_id") or details.get("generation_id"),
            "cost_usd": usage.get("cost"),
            "is_byok": usage.get("is_byok"),
            "estimated_cost_usd": details.get("estimated_cost_usd"),
            "requested_seconds": scene.duration_ms / 1000,
            "billed_seconds": details.get("billed_seconds"),
            "seed": details.get("seed"),
            "resolution": self.resolution,
            "aspect_ratio": self.aspect_ratio,
            "reference_count": details.get("reference_count"),
            "identity_reference_count": details.get("identity_reference_count"),
            "prompt": details.get("prompt"),
            "polls": polls,
            "generation_seconds": round(generation_seconds, 3),
            "output": normalized.probe.as_dict(),
            "source": normalized.source.as_dict(),
            "ledger": "resumed" if resumed else "new",
        }
        if details.get("edit"):  # 부모 클립 편집을 시도했다 (applied: 편집으로 만들었는가)
            metadata["edit"] = details["edit"]
        done = replace(
            record,
            status=COMPLETED,
            clip_uri=uri,
            clip_metadata=metadata,
            error=None,
            updated_at=self._wall_clock(),
        )
        edit = details.get("edit")
        transient = (
            isinstance(edit, Mapping)
            and edit.get("applied") is False
            and edit.get("transient") is True
        )
        if transient and self._share_regenerated(request, done):
            # 일시적인 사정으로 새로 생성한 결과 (ADR-0021): 조건 키에 남겼으니 편집 요청의 키는 비운다.
            # 다음에 같은 요청이 오면 편집을 다시 시도한다.
            # 조건 키에 먼저 쓰고 나서 비운다 (그 사이에 끊겨도 편집 키의 접수 기록으로 이어 받는다)
            self._remember(replace(done, status=RELEASED))
            metadata = {**metadata, "provisional": True}
        else:
            self._remember(done)
            if not transient:
                self._share_regenerated(request, done)
        if not resumed:  # 이어 받은 작업의 시간은 남은 대기 시간일 뿐이다
            self._note_seconds(str(metadata["model"]), generation_seconds)
        log.info(
            "OpenRouter 영상 완료: job=%s cost=%s polls=%d %.1fs",
            job_id,
            metadata["cost_usd"],
            polls,
            generation_seconds,
        )
        return GeneratedClip(
            scene_key=scene.scene_key,
            uri=uri,
            duration_ms=scene.duration_ms,
            fingerprint=request.fingerprint,
            metadata=metadata,
        )

    def _share_regenerated(self, request: SceneGenerationRequest, record: JobRecord) -> bool:
        """편집하지 못해 새로 생성한 결과를 편집 설정과 무관한 키(조건 + 정체성 기준)로도 남긴다.

        그 결과는 편집을 끈 설정이 만들었을 결과와 같다. 편집 키로만 남기면 편집 모드·편집 모델을 바꾸거나 껐을 때
        같은 장면을 또 생성(과금)한다. 편집 시도 기록은 이 요청의 것이므로 함께 남기지 않는다.

        돌려주는 값: 그 키에 쓸 수 있는 결과가 있는가 (지금 남겼거나 이미 있었다). 그 키로 다른 작업이 진행 중이면
        덮어쓰지 않고 False 다.

        그 키는 이 요청의 키가 아니므로 그 키의 잠금 안에서 읽고 쓴다. 다른 요청이 지금 그 키로 만들고 있으면
        (잠금을 쥐고 제출·대기하는 중) 기다리지 않고 물러난다: 상대는 몇 분씩 쥐고 있을 수 있고, 상대가 접수 기록을
        남기기 전에 읽으면 '없음' 으로 보여 그 기록과 서로 덮어쓰게 된다 (감사 A-09).
        """
        edit = record.details.get("edit")
        if not isinstance(edit, Mapping) or edit.get("applied") is not False:
            return False
        plain_key = generation_key(request.generation_fingerprint, self.profile())
        if plain_key == record.key:
            return False
        with self._locks.try_hold(plain_key) as free:
            if not free:
                return False  # 그 키로 지금 만들고 있는 요청이 있다
            existing = self._resumable(plain_key)
            if existing is not None:
                if existing.status != COMPLETED:
                    return False  # 그 키로 진행 중인 작업이 있다. 덮어쓰지 않는다 (이어 받을 수 있어야 한다)
                if self._has_clip(existing):
                    return True  # 이미 쓸 수 있는 결과가 있다. 그대로 둔다
            private = ("edit", "provisional")  # 이 요청의 것: 조건 키의 결과에는 싣지 않는다
            return self._remember(
                replace(
                    record,
                    key=plain_key,
                    details={k: v for k, v in record.details.items() if k != "edit"},
                    clip_metadata={
                        k: v for k, v in record.clip_metadata.items() if k not in private
                    },
                )
            )

    def _reuse(self, request: SceneGenerationRequest, record: JobRecord) -> GeneratedClip:
        """원장에 클립까지 저장된 작업: 제출·폴링·다운로드 없이 그 클립을 돌려준다."""
        assert record.clip_uri is not None
        log.info(
            "OpenRouter 작업 원장의 완료된 결과를 다시 씀 (제출·과금 없음): job=%s clip=%s "
            "처음 버전=%s 이번 버전=%s",
            record.job_id,
            record.clip_uri,
            record.version_id,
            request.version_id,
        )
        return GeneratedClip(
            scene_key=request.scene.scene_key,
            uri=record.clip_uri,
            duration_ms=request.scene.duration_ms,
            fingerprint=request.fingerprint,
            metadata={**record.clip_metadata, "ledger": "reused"},
        )

    # ---- 작업 원장 ----
    def _resumable(self, key: str) -> JobRecord | None:
        """이어 받을 수 있는 원장 항목. 없음·실패·비움(released)·만료·읽기 실패면 None (새로 시도).

        기한은 제공자에게 다시 물어야 하는 항목에만 적용한다. 클립이 저장소에 남아 있는 완료 항목은 조회도 제출도
        필요 없으므로 오래돼도 그대로 쓴다 (감사 A-32).
        """
        try:
            record = self._ledger.get(key)
        except Exception as exc:  # noqa: BLE001 - 깨진 항목 때문에 생성이 막히지 않게 한다
            log.warning(
                "OpenRouter 작업 원장을 읽지 못해 없는 것으로 봅니다 (key=%s): %s", key[:12], exc
            )
            return None
        if record is None or record.status in (FAILED, RELEASED):
            return None
        if self._has_clip(record):
            return record
        age = self._wall_clock() - record.submitted_at
        if age > self.ledger_ttl_seconds:
            log.warning(
                "OpenRouter 작업 원장 항목이 만료돼 새로 제출합니다: job=%s "
                "(제출 %.1f시간 전, 기한 %.1f시간)",
                record.job_id,
                age / 3600,
                self.ledger_ttl_seconds / 3600,
            )
            return None
        return record

    def _has_clip(self, record: JobRecord) -> bool:
        """클립까지 저장했고 그 클립이 저장소에 남아 있는 항목인가 (제공자에게 다시 물을 것이 없다)."""
        return (
            record.status == COMPLETED
            and record.clip_uri is not None
            and self._store.exists(record.clip_uri)
        )

    def _remember(self, record: JobRecord) -> bool:
        """원장에 남긴다. 남기지 못하면 False (기록 실패가 과금된 작업을 버리게 하지 않는다)."""
        try:
            self._ledger.put(record)
            return True
        except Exception as exc:  # noqa: BLE001 - 원장 기록 실패가 과금된 작업을 버리게 하지 않는다
            log.error(
                "OpenRouter 작업 원장에 기록하지 못했습니다 (job=%s, 상태 %s): %s. "
                "재시작하면 이 작업을 이어 받지 못합니다",
                record.job_id,
                record.status,
                exc,
            )
            return False

    def profile(self) -> Mapping[str, object]:
        """생성 캐시·작업 원장 키에 fingerprint 와 함께 들어가는 설정 (ADR-0013).

        모델·해상도·화면비·fps 를 바꾸면 이전 결과를 쓰지 않는다. 주소도 넣어 가짜 서버(로컬 시험)에서 만든 결과가
        실제 서버 요청에 재사용되지 않게 한다. 키는 넣지 않는다.
        """
        return {
            "generator": "openrouter",
            "endpoint": self.base_url,
            "model": self.model,
            "resolution": self.resolution,
            "aspect_ratio": self.aspect_ratio,
            "fps": self.fps,
        }

    # ---- 모델 메타데이터 ----
    def model_info(
        self, model: str | None = None, *, timeout: float | None = None
    ) -> VideoModelInfo:
        """모델(기본: 설정한 생성 모델)의 허용 길이·화면비·해상도·seed·가격. 모델마다 한 번만 받는다 (키 불필요)."""
        model = model or self.model
        if model not in self._model_infos:
            self._model_infos[model] = self._fetch_model_info(model, timeout)
        return self._model_infos[model]

    def _fetch_model_info(self, model: str, timeout: float | None = None) -> VideoModelInfo:
        url = f"{self.base_url}/videos/models"
        try:
            resp = self._http.request("GET", url, timeout=timeout or self.request_timeout_seconds)
        except TransportError as exc:
            raise GenerationError(f"OpenRouter 영상 모델 목록을 받지 못했습니다: {exc}") from exc
        if resp.status != 200:
            raise GenerationError(
                f"OpenRouter 영상 모델 목록을 받지 못했습니다: {resp.status} {_hint(resp.status)}"
            )
        try:
            models = resp.json()["data"]
            found = next(
                (m for m in models if model in (m.get("id"), m.get("canonical_slug"))), None
            )
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise GenerationError("OpenRouter 영상 모델 목록의 형식이 예상과 다릅니다") from exc
        if found is None:
            available = ", ".join(sorted(str(m.get("id")) for m in models))
            setting = "FRAME_VIDEO_MODEL" if model == self.model else "FRAME_VIDEO_EDIT_MODEL"
            raise GenerationError(
                f"OpenRouter 영상 모델 목록에 '{model}' 이 없습니다. {setting} 을 확인하세요 "
                f"(사용 가능: {available})",
                retryable=False,
            )
        return VideoModelInfo.from_json(found)

    def _check_supported(self, info: VideoModelInfo) -> None:
        if info.aspect_ratios and self.aspect_ratio not in info.aspect_ratios:
            raise GenerationError(
                f"모델 {info.id} 은 화면비 {self.aspect_ratio} 를 지원하지 않습니다 "
                f"(지원: {', '.join(info.aspect_ratios)})",
                retryable=False,
            )
        if info.resolutions and self.resolution not in info.resolutions:
            raise GenerationError(
                f"모델 {info.id} 은 해상도 {self.resolution} 을 지원하지 않습니다 "
                f"(지원: {', '.join(info.resolutions)})",
                retryable=False,
            )

    # ---- 참조 이미지 ----
    def _input_references(
        self, refs: Sequence[ReferenceImageInput]
    ) -> tuple[list[dict[str, Any]], dict[str | None, list[int]]]:
        items: list[dict[str, Any]] = []
        numbers: dict[str | None, list[int]] = {}
        sent: dict[
            str, int
        ] = {}  # 이미지 주소 -> 번호 (여러 객체가 같은 이미지를 가리키면 한 번만 보낸다)
        for ref in refs:
            url = self._reference_url(ref)
            index = sent.get(url)
            if index is None:
                items.append({"type": "image_url", "image_url": {"url": url}})
                index = sent[url] = len(items)
            if index not in numbers.setdefault(ref.entity_key, []):
                numbers[ref.entity_key].append(index)
        return items, numbers

    def _reference_url(self, ref: ReferenceImageInput) -> str:
        """http(s)·data URI 는 그대로, 그 밖에는 ArtifactStore 에 있는 이미지만 data URI 로 보낸다.

        임의의 로컬 경로는 읽지 않는다 (API 입력으로 서버 파일을 외부에 보내는 일을 막는다).
        """
        uri = ref.image_uri
        if urlsplit(uri).scheme.lower() in {"http", "https", "data"}:
            return uri
        try:
            data = self._store.get(uri)
            media_type = self._store.content_type(uri)
        except (StorageError, OSError) as exc:
            raise GenerationError(
                f"참조 이미지를 읽을 수 없습니다: {uri} ({exc})", retryable=False
            ) from exc
        if not media_type.startswith("image/"):
            raise GenerationError(
                f"참조 이미지는 이미지 형식이어야 합니다: {uri} ({media_type})", retryable=False
            )
        return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"

    # ---- HTTP 단계 ----
    def _submit(self, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {**self._auth_headers(), "Content-Type": "application/json"}
        for attempt in range(1, self.submit_attempts + 1):
            # 여기까지 오는 동안(잠금·부모 주소·앞선 429 뒤의 대기) 호출한 쪽이 마감으로 돌아갔으면 접수시키지 않는다
            stop_if_abandoned("제출하지 않습니다")
            try:
                resp = self._http.request(
                    "POST",
                    f"{self.base_url}/videos",
                    headers=headers,
                    body=payload,
                    timeout=self.request_timeout_seconds,
                )
            except TransportError as exc:
                raise GenerationError(
                    f"OpenRouter 제출 응답을 받지 못했습니다 ({exc}). 작업이 접수됐을 수 있어 다시 보내지 않습니다. "
                    "OpenRouter 활동 기록(Activity)에서 확인하세요",
                    retryable=False,
                ) from exc
            if resp.status in (200, 201, 202):
                return self._json(resp)
            if resp.status in _RETRY_SUBMIT and attempt < self.submit_attempts:
                delay = self._retry_delay(resp, attempt)
                log.warning(
                    "OpenRouter 제출 %s, %.0f초 뒤 다시 보냄 (%d/%d)",
                    resp.status,
                    delay,
                    attempt,
                    self.submit_attempts,
                )
                self._sleep(delay)
                continue
            error = self._http_error("제출", resp, retryable=resp.status in _RETRY_SUBMIT)
            raise _SubmitRejected(str(error), status=resp.status, retryable=error.retryable)
        raise AssertionError("unreachable")  # pragma: no cover

    def _wait(
        self, job_id: str, accepted_at: float, *, poll_now: bool = False
    ) -> tuple[dict[str, Any], int]:
        """완료될 때까지 폴링한다. poll_now 면 첫 조회를 기다리지 않는다 (이어 받은 작업은 이미 끝났을 수 있다)."""
        deadline = accepted_at + self.timeout_seconds
        polls = 0
        last_state = "pending"
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise GenerationTimeout(
                    f"OpenRouter 작업 {job_id} 이 {self.timeout_seconds:g}초 안에 끝나지 않았습니다 "
                    f"(마지막 상태: {last_state}). 작업은 계속 진행돼 과금될 수 있습니다. "
                    "같은 조건으로 다시 생성하면 새로 제출하지 않고 이 작업을 이어 받습니다 "
                    f"(작업 원장, 제출 뒤 {self.ledger_ttl_seconds / 3600:g}시간 안)",
                    retryable=False,
                )
            if not (poll_now and polls == 0):
                self._sleep(min(self.poll_interval_seconds, remaining))
            polls += 1
            try:
                resp = self._http.request(
                    "GET",
                    self._job_url(job_id),
                    headers=self._auth_headers(),
                    timeout=self.request_timeout_seconds,
                )
            except TransportError as exc:
                log.warning("OpenRouter 상태 조회 실패 (job=%s): %s, 계속 기다림", job_id, exc)
                continue
            if _may_pass(resp.status):
                log.warning("OpenRouter 상태 조회 %s (job=%s), 계속 기다림", resp.status, job_id)
                continue
            if resp.status in _GONE:  # 만료됐거나 다른 서버의 작업: 이어 받을 수 없다
                raise self._gone("상태 조회", resp, job_id)
            if resp.status != 200:
                raise self._http_error("상태 조회", resp, retryable=False, job_id=job_id)
            data = self._json(resp, job_id)
            state = str(data.get("status") or "")
            last_state = state or last_state
            if state == "completed":
                return data, polls
            if state in _FAILED_STATES:
                reason = self._scrub(str(data.get("error") or "사유 없음"))
                raise _JobUnrecoverable(
                    f"OpenRouter 작업 {job_id} 이 {state} 상태로 끝났습니다: {reason}. "
                    "과금 여부를 알 수 없어 다시 제출하지 않습니다"
                )

    def _download(self, job_id: str) -> bytes:
        url = f"{self._job_url(job_id)}/content?index=0"
        last = ""
        for attempt in range(1, self.download_attempts + 1):
            try:
                resp = self._http.request(
                    "GET",
                    url,
                    headers=self._auth_headers(),
                    timeout=max(self.request_timeout_seconds, 300.0),
                )
            except TransportError as exc:
                last = str(exc)
            else:
                if resp.status == 200:
                    if not _looks_like_video(resp):
                        raise _JobUnrecoverable(
                            f"OpenRouter 작업 {job_id} 의 결과가 영상이 아닙니다 "
                            f"(Content-Type: {resp.content_type or '없음'})"
                        )
                    return resp.body
                if resp.status in _GONE:
                    raise self._gone("결과 다운로드", resp, job_id)
                if not _may_pass(resp.status):
                    raise self._http_error("결과 다운로드", resp, retryable=False, job_id=job_id)
                last = f"{resp.status} {_hint(resp.status)}"
            if attempt < self.download_attempts:
                self._sleep(min(30.0, 5.0 * attempt))
        raise GenerationError(
            f"OpenRouter 작업 {job_id} 의 결과를 내려받지 못했습니다 ({last}). 작업은 끝났으니 같은 조건으로 "
            "다시 생성하면 새로 제출하지 않고 결과만 다시 받습니다 (작업 원장)",
            retryable=False,
        )

    def _normalize(
        self, request: SceneGenerationRequest, raw: bytes, job_id: str
    ) -> NormalizedVideo:
        width, height = self.output_size
        try:
            return self._normalizer.normalize(
                raw, duration_ms=request.scene.duration_ms, fps=self.fps, width=width, height=height
            )
        except Exception as exc:  # 이미 과금된 원본은 남기고, 다시 제출하지 않는다
            key = f"clips/{request.version_id}/{request.scene.scene_key}.source.mp4"
            try:
                kept = f" 원본은 {self._store.put(key, raw, 'video/mp4')} 에 남겼습니다"
            except Exception:  # noqa: BLE001 - 원본 보관 실패가 원래 오류를 가리지 않게 한다
                kept = ""
            raise GenerationError(
                f"OpenRouter 작업 {job_id} 의 영상을 정규화하지 못했습니다: {exc}.{kept}",
                retryable=False,
            ) from exc

    # ---- 보조 ----
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _job_url(self, job_id: str) -> str:
        return f"{self.base_url}/videos/{quote(job_id, safe='')}"

    def _json(self, resp: HttpResponse, job_id: str | None = None) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError as exc:
            where = f" (작업 {job_id})" if job_id else ""
            raise GenerationError(
                f"OpenRouter 응답{where}이 JSON 이 아닙니다 ({resp.status})", retryable=False
            ) from exc
        if not isinstance(data, dict):
            raise GenerationError(
                f"OpenRouter 응답 형식이 예상과 다릅니다 ({resp.status})", retryable=False
            )
        return data

    def _retry_delay(self, resp: HttpResponse, attempt: int) -> float:
        raw = resp.headers.get("retry-after", "")
        try:
            return min(60.0, max(0.0, float(raw)))
        except ValueError:
            return min(60.0, 5.0 * 2 ** (attempt - 1))

    def _http_error(
        self, stage: str, resp: HttpResponse, *, retryable: bool, job_id: str | None = None
    ) -> GenerationError:
        where = f" (작업 {job_id})" if job_id else ""
        detail = self._scrub(_error_message(resp))
        return GenerationError(
            f"OpenRouter {stage} 실패{where}: {resp.status} {_hint(resp.status)}: {detail}",
            retryable=retryable,
        )

    def _gone(self, stage: str, resp: HttpResponse, job_id: str) -> _JobUnrecoverable:
        detail = self._scrub(_error_message(resp))
        return _JobUnrecoverable(
            f"OpenRouter {stage} 실패 (작업 {job_id}): {resp.status} {_hint(resp.status)}: {detail}. "
            "만료됐거나 다른 서버의 작업이라 이어 받지 않고, 다음 생성 때 새로 제출합니다"
        )

    def _scrub(self, text: str) -> str:
        """서버 메시지에 키가 섞여 와도 그대로 옮기지 않는다."""
        cleaned = text.replace(self._api_key, "***")
        cleaned = _KEY_TOKEN.sub("sk-or-***", cleaned)
        # 제공자 메시지에 섞여 오는 주소(올려 보낸 참조 이미지의 서명 URL 등)는 호스트만 남긴다.
        # 호스트 앞의 계정 정보(user:password@)도 뗀다
        cleaned = _URL.sub(lambda m: f"{m.group(1)}://{m.group(2).rsplit('@', 1)[-1]}/...", cleaned)
        cleaned = _QUERY.sub("?...", cleaned)
        cleaned = _DATA_URI.sub(lambda m: f"{m.group(1)}...", cleaned)
        return " ".join(cleaned.split())[:400]


def _fits_in_a_header(key: str) -> bool:
    """HTTP 헤더에 실을 수 있는 키인가: 공백·제어 문자가 없는 ASCII."""
    return key.isascii() and key.isprintable() and not any(char.isspace() for char in key)


def _may_pass(status: int) -> bool:
    """조회·내려받기(GET)의 응답이 기다리면 나아질 수 있는가: 일시적인 4xx 와 모든 5xx.

    GET 은 과금과 무관해 다시 물어도 된다 (ADR-0011). 상태 조회·결과 내려받기·부모 작업 조회가 이 기준 하나를 쓴다.
    """
    return status in _TRANSIENT_GET or status >= 500


def _hint(status: int) -> str:
    if status in _STATUS_HINTS:
        return _STATUS_HINTS[status]
    return "OpenRouter 서버 오류" if status >= 500 else "예상하지 못한 응답"


def _error_message(resp: HttpResponse) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.body[:300].decode("utf-8", errors="replace") or "(본문 없음)"
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or data)


def _looks_like_video(resp: HttpResponse) -> bool:
    kind = resp.content_type
    return bool(resp.body) and (
        kind.startswith("video/") or kind in {"", "application/octet-stream"}
    )
