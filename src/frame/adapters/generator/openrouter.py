"""OpenRouter 영상 생성 API 어댑터 (FRAME_GENERATOR=external_api, ADR-0011).

흐름: 모델 메타데이터(GET /videos/models, 키 불필요) -> 제출(POST /videos) -> 폴링(GET /videos/{id})
-> 다운로드(GET /videos/{id}/content) -> ffmpeg 정규화(장면 길이·fps·해상도, 오디오 제거) -> ArtifactStore.put.
모델은 설정의 id 하나로 바꾼다 (기본 Wan 3.0). 허용 길이·화면비·해상도·seed 지원 여부는 메타데이터를 따른다.

비용 안전 규칙
- 제출 단계의 429·502 만 이 어댑터 안에서 다시 보낸다. 접수(202) 전이라 과금되지 않는다.
- 그 밖의 제출 오류, 접수 뒤 실패(failed·cancelled·expired), 시간 초과, 다운로드·정규화 실패는 retryable=False 로 보고해
  RetryingVideoGenerator 가 같은 장면을 다시 제출(재과금)하지 않게 한다.
- 폴링·다운로드(GET)의 일시 오류는 과금과 무관해 계속 시도한다.
- 키는 Authorization 헤더에만 싣고(리다이렉트로 넘기지 않음) 예외·로그·클립 메타데이터에 남기지 않는다.
  서명된 다운로드 URL(urls)도 키처럼 다뤄 기록하지 않는다.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from frame.adapters.http_transport import (
    HttpResponse,
    HttpTransport,
    TransportError,
    UrllibTransport,
)
from frame.application.errors import (
    ConfigurationError,
    GenerationError,
    GenerationTimeout,
    StorageError,
)
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.generator import (
    GeneratedClip,
    SceneGenerationRequest,
    VideoGenerator,
)
from frame.application.ports.structurer import ReferenceImageInput

from .ffmpeg import FfmpegVideoNormalizer, NormalizedVideo, VideoNormalizer, resolve_ffmpeg

log = logging.getLogger(__name__)

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_VIDEO_MODEL = "alibaba/wan-3.0"
KEY_ENV = "OPENROUTER_API_KEY"

_KEY_TOKEN = re.compile(r"sk-or-[A-Za-z0-9_\-]+")
_RETRY_SUBMIT = frozenset({429, 502})  # 접수 전 거절: 과금 없음
_TRANSIENT_GET = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_FAILED_STATES = frozenset({"failed", "cancelled", "expired"})
_MAX_SEED = 2**31  # 제공자 대부분(Wan 등)이 0 ~ 2^31-1 범위의 seed 를 받는다
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
        match = _KEY_TOKEN.search(path.read_text(encoding="utf-8", errors="ignore"))
        if match:
            return match.group(0)
        raise ConfigurationError(f"키 파일에서 OpenRouter 키(sk-or-...)를 찾지 못했습니다: {path}")
    raise ConfigurationError(
        f"OpenRouter API 키가 없습니다. {KEY_ENV} 환경 변수 또는 FRAME_VIDEO_KEY_FILE 을 설정하세요"
    )


def mask_key(key: str) -> str:
    """진단용 표시. 끝 4자리만 (ADR-0010)."""
    return f"...{key[-4:]}" if len(key) > 8 else "***"


# ---- 모델 메타데이터 ---------------------------------------------------------------------


@dataclass(frozen=True)
class VideoModelInfo:
    """GET /api/v1/videos/models 의 항목 중 이 어댑터가 쓰는 필드."""

    id: str
    durations: tuple[int, ...] = ()
    aspect_ratios: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ()
    seed: bool = False
    pricing: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> VideoModelInfo:
        return cls(
            id=str(data["id"]),
            durations=tuple(sorted(int(d) for d in data.get("supported_durations") or ())),
            aspect_ratios=tuple(str(a) for a in data.get("supported_aspect_ratios") or ()),
            resolutions=tuple(str(r) for r in data.get("supported_resolutions") or ()),
            seed=bool(data.get("seed")),
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
    """
    numbers = reference_numbers or {}
    scene = request.scene
    lines: list[str] = []
    if scene.description.strip():
        lines.append(f"Scene: {scene.description.strip()}")
    subjects: list[str] = []
    for entity in request.entities:
        details = [entity.description.strip()] if entity.description.strip() else []
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
    return "\n".join(lines)


def _words(predicate: str) -> str:
    return predicate.replace("_", " ")


def _reference_phrase(numbers: Sequence[int]) -> str:
    joined = ", ".join(str(n) for n in numbers)
    return f"reference image {joined}" if len(numbers) == 1 else f"reference images {joined}"


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
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ConfigurationError("OpenRouter API 키가 비어 있습니다")
        if fps <= 0 or timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ConfigurationError("fps·시간 제한·폴링 간격은 0 보다 커야 합니다")
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
        self._model_info: VideoModelInfo | None = None

    # ---- 포트 구현 ----
    def generate_scene(self, request: SceneGenerationRequest) -> GeneratedClip:
        info = self.model_info()
        self._check_supported(info)
        scene = request.scene
        seconds = info.pick_duration(scene.duration_ms)
        references, numbers = self._input_references(request.references)
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

        job = self._submit(body)
        job_id = job.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise GenerationError(
                "OpenRouter 가 작업을 받았지만 작업 id 가 없습니다. 활동 기록(Activity)을 확인하세요",
                retryable=False,
            )
        log.info(
            "OpenRouter 영상 작업 접수: job=%s model=%s duration=%ss resolution=%s scene=%s",
            job_id,
            self.model,
            seconds,
            self.resolution,
            scene.scene_key,
        )
        accepted_at = self._monotonic()
        final, polls = self._wait(job_id, accepted_at)
        generation_seconds = self._monotonic() - accepted_at
        raw = self._download(job_id)
        normalized = self._normalize(request, raw, job_id)
        uri = self._store.put(
            f"clips/{request.version_id}/{scene.scene_key}.mp4", normalized.data, "video/mp4"
        )

        usage = final.get("usage") if isinstance(final.get("usage"), dict) else {}
        price = info.price_per_second(self.resolution)
        metadata: dict[str, object] = {
            "generator": "openrouter",
            "model": self.model,
            "job_id": job_id,
            "generation_id": final.get("generation_id") or job.get("generation_id"),
            "cost_usd": usage.get("cost"),
            "is_byok": usage.get("is_byok"),
            "estimated_cost_usd": round(price * seconds, 6) if price is not None else None,
            "requested_seconds": scene.duration_ms / 1000,
            "billed_seconds": seconds,
            "seed": seed,
            "resolution": self.resolution,
            "aspect_ratio": self.aspect_ratio,
            "reference_count": len(references),
            "prompt": prompt,
            "polls": polls,
            "generation_seconds": round(generation_seconds, 3),
            "output": normalized.probe.as_dict(),
            "source": normalized.source.as_dict(),
        }
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
    def model_info(self) -> VideoModelInfo:
        """설정한 모델의 허용 길이·화면비·해상도·seed·가격. 처음 한 번만 받는다 (키 불필요)."""
        if self._model_info is None:
            self._model_info = self._fetch_model_info()
        return self._model_info

    def _fetch_model_info(self) -> VideoModelInfo:
        url = f"{self.base_url}/videos/models"
        try:
            resp = self._http.request("GET", url, timeout=self.request_timeout_seconds)
        except TransportError as exc:
            raise GenerationError(f"OpenRouter 영상 모델 목록을 받지 못했습니다: {exc}") from exc
        if resp.status != 200:
            raise GenerationError(
                f"OpenRouter 영상 모델 목록을 받지 못했습니다: {resp.status} {_hint(resp.status)}"
            )
        try:
            models = resp.json()["data"]
            found = next(
                (m for m in models if self.model in (m.get("id"), m.get("canonical_slug"))), None
            )
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise GenerationError("OpenRouter 영상 모델 목록의 형식이 예상과 다릅니다") from exc
        if found is None:
            available = ", ".join(sorted(str(m.get("id")) for m in models))
            raise GenerationError(
                f"OpenRouter 영상 모델 목록에 '{self.model}' 이 없습니다. FRAME_VIDEO_MODEL 을 확인하세요 "
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
        for index, ref in enumerate(refs, start=1):
            items.append({"type": "image_url", "image_url": {"url": self._reference_url(ref)}})
            numbers.setdefault(ref.entity_key, []).append(index)
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
            raise self._http_error("제출", resp, retryable=resp.status in _RETRY_SUBMIT)
        raise AssertionError("unreachable")  # pragma: no cover

    def _wait(self, job_id: str, accepted_at: float) -> tuple[dict[str, Any], int]:
        deadline = accepted_at + self.timeout_seconds
        polls = 0
        last_state = "pending"
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise GenerationTimeout(
                    f"OpenRouter 작업 {job_id} 이 {self.timeout_seconds:g}초 안에 끝나지 않았습니다 "
                    f"(마지막 상태: {last_state}). 작업은 계속 진행돼 과금될 수 있으니 다시 제출하지 말고 "
                    f"GET /api/v1/videos/{job_id} 로 결과를 확인하세요",
                    retryable=False,
                )
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
            if resp.status in _TRANSIENT_GET:
                log.warning("OpenRouter 상태 조회 %s (job=%s), 계속 기다림", resp.status, job_id)
                continue
            if resp.status != 200:
                raise self._http_error("상태 조회", resp, retryable=False, job_id=job_id)
            data = self._json(resp, job_id)
            state = str(data.get("status") or "")
            last_state = state or last_state
            if state == "completed":
                return data, polls
            if state in _FAILED_STATES:
                reason = self._scrub(str(data.get("error") or "사유 없음"))
                raise GenerationError(
                    f"OpenRouter 작업 {job_id} 이 {state} 상태로 끝났습니다: {reason}. "
                    "과금 여부를 알 수 없어 다시 제출하지 않습니다",
                    retryable=False,
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
                        raise GenerationError(
                            f"OpenRouter 작업 {job_id} 의 결과가 영상이 아닙니다 "
                            f"(Content-Type: {resp.content_type or '없음'})",
                            retryable=False,
                        )
                    return resp.body
                if resp.status not in _TRANSIENT_GET:
                    raise self._http_error("결과 다운로드", resp, retryable=False, job_id=job_id)
                last = f"{resp.status} {_hint(resp.status)}"
            if attempt < self.download_attempts:
                self._sleep(min(30.0, 5.0 * attempt))
        raise GenerationError(
            f"OpenRouter 작업 {job_id} 의 결과를 내려받지 못했습니다 ({last}). 작업은 끝났으니 다시 제출하지 말고 "
            f"GET /api/v1/videos/{job_id}/content 로 받으세요",
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
            except Exception:  # 원본 보관 실패가 원래 오류를 가리지 않게 한다
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

    def _scrub(self, text: str) -> str:
        """서버 메시지에 키가 섞여 와도 그대로 옮기지 않는다."""
        cleaned = text.replace(self._api_key, "***")
        cleaned = _KEY_TOKEN.sub("sk-or-***", cleaned)
        return " ".join(cleaned.split())[:300]


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
