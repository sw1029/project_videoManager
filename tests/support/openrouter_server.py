"""루프백(127.0.0.1) 가짜 OpenRouter 영상 API 서버. 실제 urllib 전송·ffmpeg·DB 를 거치는 통합 테스트용.

모델 흉내
- 보통 요청: 요청마다 다른 무늬의 작은 영상을 돌려준다 (장면을 새로 만들면 화면이 달라진다).
- 영상 참조(input_references 의 video_url)가 있는 요청: 그 주소가 가리키는 작업의 영상을 그대로 돌려준다
  (편집 모델은 화면을 거의 유지한다). 주소는 작업 상태 응답의 urls(저장본 주소)로 내준 것이어야 한다.
  data URI 는 실제 OpenRouter 처럼 400 으로 거절한다.
- previous_job_id 가 있는 요청: 400 으로 거절한다 (연속을 지원하지 않는 모델).
"""

from __future__ import annotations

import http.server
import json
import random
import string
import subprocess
import threading
from pathlib import Path
from typing import Any

MODEL: dict[str, Any] = {
    "id": "alibaba/wan-3.0",
    "supported_resolutions": ["360p", "480p", "720p"],
    "supported_aspect_ratios": ["16:9", "9:16"],
    "supported_durations": list(range(2, 31)),
    "generate_audio": True,
    "seed": True,
    "pricing_skus": {"duration_seconds_360p": "0.03", "duration_seconds_480p": "0.05"},
}
_SOURCES = ("testsrc2", "smptebars", "rgbtestsrc", "pal75bars", "yuvtestsrc", "testsrc")


class FakeOpenRouterServer:
    def __init__(self, ffmpeg: str, folder: Path) -> None:
        self.ffmpeg = ffmpeg
        self.folder = folder
        self.submits: list[dict[str, Any]] = []  # 받은 제출 본문 (거절한 것 포함)
        self.rejected = 0
        self._jobs: dict[str, bytes] = {}
        self._lock = threading.Lock()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server 규약
                path = self.path.split("?", 1)[0]
                if path == "/api/v1/videos/models":
                    return self._json(200, {"data": [MODEL]})
                job_id, _, tail = path.removeprefix("/api/v1/videos/").partition("/")
                with outer._lock:
                    video = outer._jobs.get(job_id)
                if video is None:
                    return self._json(404, {"error": {"code": 404, "message": "not found"}})
                if tail == "content":
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Length", str(len(video)))
                    self.end_headers()
                    self.wfile.write(video)
                    return None
                return self._json(
                    200,
                    {
                        "id": job_id,
                        "generation_id": job_id,
                        "status": "completed",
                        "usage": {"cost": 0.1, "is_byok": False},
                        "urls": [outer.stored_url(job_id)],  # 저장본의 (서명) 내려받기 주소
                    },
                )

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                with outer._lock:
                    outer.submits.append(body)
                    index = len(outer.submits)
                if "previous_job_id" in body:
                    with outer._lock:
                        outer.rejected += 1
                    return self._json(
                        400,
                        {"error": {"code": 400, "message": "previous_job_id is not supported"}},
                    )
                if any(url.startswith("data:") for url in outer.video_references(body)):
                    with outer._lock:
                        outer.rejected += 1
                    message = (
                        "Invalid reference URL: input_references[0].video_url.url: "
                        "Only HTTPS URLs are allowed"
                    )
                    return self._json(400, {"error": {"code": 400, "message": message}})
                job_id = "gen-vid-1794500000-" + "".join(
                    random.choices(string.ascii_letters + string.digits, k=20)
                )
                video = outer._video_for(body, index)
                with outer._lock:
                    outer._jobs[job_id] = video
                return self._json(
                    202,
                    {"id": job_id, "polling_url": f"/api/v1/videos/{job_id}", "status": "pending"},
                )

            def _json(self, code, payload):
                data = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # 테스트 출력을 조용히
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/api/v1"

    def stored_url(self, job_id: str) -> str:
        origin = self.base_url.removesuffix("/api/v1")
        return f"{origin}/files/{job_id}.mp4?signature=FAKE-SIGNED"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # ---- 본문 읽기 도우미 ----
    @staticmethod
    def image_references(body: dict[str, Any]) -> list[str]:
        return [
            r["image_url"]["url"]
            for r in body.get("input_references") or []
            if r.get("type") == "image_url"
        ]

    @staticmethod
    def video_references(body: dict[str, Any]) -> list[str]:
        return [
            r["video_url"]["url"]
            for r in body.get("input_references") or []
            if r.get("type") == "video_url"
        ]

    def _video_for(self, body: dict[str, Any], index: int) -> bytes:
        videos = self.video_references(body)
        if videos:  # 편집: 주소가 가리키는 작업의 영상을 그대로 돌려준다
            job_id = videos[0].split("/files/", 1)[1].split(".mp4", 1)[0]
            with self._lock:
                return self._jobs[job_id]
        seconds = int(body.get("duration") or 2)
        path = self.folder / f"upstream-{index}.mp4"
        source = _SOURCES[index % len(_SOURCES)]
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i"]
        cmd += [f"{source}=size=90x160:rate=10:duration={seconds}"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
        subprocess.run(cmd, check=True, capture_output=True)
        return path.read_bytes()
