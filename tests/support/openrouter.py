"""OpenRouter 영상 API 테스트 더블 (네트워크·ffmpeg 없음).

- FakeOpenRouterApi: HttpTransport 를 구현한 가짜 서버. 경로별 응답 대기열을 두고 모든 요청을 기록한다.
- FakeNormalizer: ffmpeg 대신 정규화 결과(프레임 수·해상도)를 계산만 해서 돌려준다.
- FakeTime: sleep 이 시계를 앞으로 돌리는 가짜 시간 (폴링 간격·시간 초과를 즉시 검증).
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from frame.adapters.generator.normalizer import NormalizedVideo, VideoNormalizer
from frame.adapters.generator.openrouter import OpenRouterVideoGenerator
from frame.adapters.shared.http import HttpResponse, HttpTransport
from frame.adapters.shared.media.probe import VideoProbe
from frame.application.ports.artifact_store import ArtifactStore

API_BASE = "https://openrouter.ai/api/v1"
TEST_KEY = "sk-or-v1-" + "f00d" * 16  # 가짜 키. 예외·메타데이터·로그에 나오면 안 된다
JOB_ID = "gen-vid-1789480874-Ab3dEf9hIjKlMnOpQrSt"
SIGNED_URL = "https://storage.example/out.mp4?signature=SIGNED-SECRET"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

# 2026-11-11 GET /api/v1/videos/models 응답의 alibaba/wan-3.0 항목에서 쓰는 필드만 옮김
WAN_30: dict[str, Any] = {
    "id": "alibaba/wan-3.0",
    "canonical_slug": "alibaba/wan-3.0-20260824",
    "supported_resolutions": ["480p", "720p", "1080p"],
    "supported_aspect_ratios": ["16:9", "4:3", "1:1", "3:4", "9:16"],
    "supported_sizes": None,
    "supported_durations": list(range(2, 31)),
    "supported_frame_images": ["first_frame", "last_frame"],
    "generate_audio": True,
    "seed": True,
    "pricing_skus": {
        "duration_seconds_480p": "0.05",
        "duration_seconds_720p": "0.1",
        "duration_seconds_1080p": "0.2",
    },
}
# seed 를 받지 않고 길이가 5·10초뿐인 모델 (길이 선택·seed 생략 검증용)
NO_SEED_5_10: dict[str, Any] = {
    "id": "vendor/no-seed",
    "supported_resolutions": ["720p"],
    "supported_aspect_ratios": ["16:9", "9:16"],
    "supported_durations": [5, 10],
    "seed": False,
    "pricing_skus": {"duration_seconds": "0.1"},
}
LANDSCAPE_ONLY: dict[str, Any] = {
    "id": "vendor/landscape-only",
    "supported_resolutions": ["720p"],
    "supported_aspect_ratios": ["16:9"],
    "supported_durations": [4, 8],
    "seed": True,
    "pricing_skus": {},
}


def json_response(
    status: int, payload: Any, headers: Mapping[str, str] | None = None
) -> HttpResponse:
    merged = {"content-type": "application/json"}
    merged.update({k.lower(): v for k, v in (headers or {}).items()})
    return HttpResponse(status, json.dumps(payload).encode("utf-8"), merged)


def error_response(
    status: int, message: str, headers: Mapping[str, str] | None = None
) -> HttpResponse:
    return json_response(status, {"error": {"code": status, "message": message}}, headers)


def accepted(job_id: str = JOB_ID) -> HttpResponse:
    return json_response(
        202,
        {"id": job_id, "polling_url": f"/api/v1/videos/{job_id}", "status": "pending"},
    )


def status(state: str, job_id: str = JOB_ID, **extra: Any) -> HttpResponse:
    payload = {"id": job_id, "polling_url": f"/api/v1/videos/{job_id}", "status": state}
    payload.update(extra)
    return json_response(200, payload)


def completed(job_id: str = JOB_ID, cost: float | None = 0.2) -> HttpResponse:
    return status(
        "completed",
        job_id,
        generation_id="gen-xyz789",
        unsigned_urls=[f"{API_BASE}/videos/{job_id}/content?index=0"],
        urls=[SIGNED_URL],
        expires_at=1790000000,
        usage={"cost": cost, "is_byok": False},
    )


def video_content(data: bytes = b"RAW-MP4-BYTES") -> HttpResponse:
    return HttpResponse(200, data, {"content-type": "video/mp4"})


@dataclass
class RecordedCall:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout: float

    @property
    def path(self) -> str:
        return urlsplit(self.url).path

    @property
    def query(self) -> dict[str, list[str]]:
        return parse_qs(urlsplit(self.url).query)

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def json(self) -> Any:
        assert self.body is not None
        return json.loads(self.body)


class FakeOpenRouterApi(HttpTransport):
    """경로별 응답 대기열. 대기열에 하나만 남으면 그 응답을 계속 돌려준다. 예외를 넣으면 그 예외를 던진다."""

    def __init__(
        self, models: Iterable[Mapping[str, Any]] = (WAN_30,), job_id: str = JOB_ID
    ) -> None:
        self.calls: list[RecordedCall] = []
        self.job_id = job_id
        self._routes: dict[tuple[str, str], deque[HttpResponse | BaseException]] = {}
        self.on("GET", "/api/v1/videos/models", json_response(200, {"data": list(models)}))
        self.on("POST", "/api/v1/videos", accepted(job_id))
        self.on("GET", f"/api/v1/videos/{job_id}", status("in_progress", job_id), completed(job_id))
        self.on("GET", f"/api/v1/videos/{job_id}/content", video_content())

    def on(
        self, method: str, path: str, *responses: HttpResponse | BaseException
    ) -> FakeOpenRouterApi:
        self._routes[(method, path)] = deque(responses)
        return self

    def add_job(self, job_id: str, *statuses: HttpResponse | BaseException) -> FakeOpenRouterApi:
        """다른 id 의 작업을 하나 더 둔다 (상태 응답 대기열과 결과 영상). 제출 순서는 POST 대기열로 정한다."""
        self.on(
            "GET",
            f"/api/v1/videos/{job_id}",
            *(statuses or (status("in_progress", job_id), completed(job_id))),
        )
        self.on("GET", f"/api/v1/videos/{job_id}/content", video_content(f"RAW-{job_id}".encode()))
        return self

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 60.0,
    ) -> HttpResponse:
        call = RecordedCall(method, url, dict(headers or {}), body, timeout)
        self.calls.append(call)
        queue = self._routes.get((method, call.path))
        if not queue:
            return error_response(404, "Resource not found")
        item = queue.popleft() if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def calls_to(self, method: str, path: str) -> list[RecordedCall]:
        return [c for c in self.calls if c.method == method and c.path == path]

    @property
    def submits(self) -> list[RecordedCall]:
        return self.calls_to("POST", "/api/v1/videos")


SOURCE_PROBE = VideoProbe(
    width=480, height=832, fps=16.0, frames=65, duration_ms=4063, has_audio=True, codec="h264"
)


class FakeNormalizer(VideoNormalizer):
    """ffmpeg 없이 '정규화했다면 나왔을' 사양을 계산해 돌려준다."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._error = error

    def normalize(
        self, source: bytes, *, duration_ms: int, fps: int, width: int, height: int
    ) -> NormalizedVideo:
        self.calls.append(
            {
                "source": source,
                "duration_ms": duration_ms,
                "fps": fps,
                "width": width,
                "height": height,
            }
        )
        if self._error is not None:
            raise self._error
        frames = round(duration_ms * fps / 1000)
        probe = VideoProbe(
            width=width,
            height=height,
            fps=float(fps),
            frames=frames,
            duration_ms=round(frames * 1000 / fps),
            has_audio=False,
            codec="h264",
        )
        return NormalizedVideo(data=b"MP4:" + source, probe=probe, source=SOURCE_PROBE)


class FakeTime:
    """sleep 하면 시계가 그만큼 앞으로 간다. time() 은 같은 만큼 움직이는 벽시계(작업 원장의 제출 시각)."""

    def __init__(self, wall: float = 1_789_480_000.0) -> None:
        self.now = 1000.0
        self.wall = wall
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.wall + (self.now - 1000.0)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_openrouter_generator(
    store: ArtifactStore,
    api: FakeOpenRouterApi | None = None,
    normalizer: VideoNormalizer | None = None,
    clock: FakeTime | None = None,
    **overrides: Any,
) -> OpenRouterVideoGenerator:
    clock = clock or FakeTime()
    options: dict[str, Any] = {
        "store": store,
        "api_key": TEST_KEY,
        "model": "alibaba/wan-3.0",
        "resolution": "480p",
        "aspect_ratio": "9:16",
        "fps": 30,
        "base_url": "https://openrouter.ai/api/v1",
        "timeout_seconds": 600.0,
        "poll_interval_seconds": 20.0,
        "edit_mode": "off",
        "edit_parent_wait_seconds": 10.0,
        "transport": api or FakeOpenRouterApi(),
        "normalizer": normalizer or FakeNormalizer(),
        "sleep": clock.sleep,
        "monotonic": clock.monotonic,
        "wall_clock": clock.time,
    }
    options.update(overrides)
    return OpenRouterVideoGenerator(**options)
