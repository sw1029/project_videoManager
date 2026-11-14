"""프론트 연동 계약 (ADR-0009).

통일된 오류 형식, 상태 확인 정보, 영상·클립 조회, 장면 수준 수정, CORS·정적 서빙·요청 제한·입력 제한.
"""

from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient

from frame.adapters.storage import InMemoryArtifactStore, LocalFileArtifactStore
from frame.application.errors import ConfigurationError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.generator import GeneratedClip, VideoArtifact, VideoAssembler
from frame.config import Settings
from frame.presentation.app import create_app
from tests.support.factories import CAFE_PROMPT, dispatcher_of, make_container

SCENE_LEVEL_DSL = (
    "scene a 0-4000 : 바리스타가 컵을 들어 보인다\nscene b 4000-8000 : 컵 클로즈업\n"
    "entity barista PERSON\nentity cup PROP\nin a: barista, cup\nin b: cup\n"
    "cup.color = red\nscene.location = cafe counter\nscene.lighting = warm @ a"
)


def _client(settings: Settings | None = None, **overrides) -> tuple[TestClient, object]:
    container = make_container(settings, **overrides)
    return TestClient(create_app(container), raise_server_exceptions=False), container


def _settings(**kwargs) -> Settings:
    return Settings(jobs="recording", persistence="memory", **kwargs)


def _create(client, container, prompt=CAFE_PROMPT, seconds=None) -> dict:
    body = {"title": "카페", "prompt": prompt, "reference_images": []}
    if seconds is not None:
        body["target_duration_ms"] = seconds * 1000
    response = client.post("/api/projects", json=body)
    assert response.status_code == 202, response.text
    dispatcher_of(container).run_all()
    return client.get(f"/api/versions/{response.json()['version_id']}").json()


def _generate(client, container, version_id: str) -> dict:
    assert client.post(f"/api/versions/{version_id}/generations").status_code == 202
    dispatcher_of(container).run_all()
    return client.get(f"/api/versions/{version_id}").json()


# ---- 오류 형식 통일 ---------------------------------------------------------------


def test_schema_validation_errors_use_the_common_error_shape():
    client, _ = _client()
    response = client.post("/api/projects", json={"title": "", "prompt": "x"})
    body = response.json()
    assert response.status_code == 422
    assert body["error_code"] == "VALIDATION_ERROR"
    assert "title" in body["error_message"]
    assert body["details"][0]["loc"] == "body.title"
    assert body["details"][0]["message"]


def test_unknown_route_and_wrong_method_use_the_common_error_shape():
    client, _ = _client()
    missing = client.get("/api/nothing-here")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "NOT_FOUND"
    wrong = client.delete("/api/projects")
    assert wrong.status_code == 405
    assert wrong.json()["error_code"] == "METHOD_NOT_ALLOWED"
    assert "POST" in wrong.headers["allow"]


def test_unexpected_exceptions_return_json_500(monkeypatch):
    client, container = _client()

    def boom(*_args, **_kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(container.queries, "get_version", boom)
    response = client.get("/api/versions/ver_x")
    assert response.status_code == 500
    assert response.json()["error_code"] == "INTERNAL_ERROR"
    assert "secret" not in response.json()["error_message"]


# ---- 상태 확인 ----------------------------------------------------------------------


def test_health_tells_the_frontend_which_input_the_structurer_expects():
    client, _ = _client()
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["version"]
    assert body["structuring_input"] == "dsl"
    assert body["adapters"]["llm_provider"] is None


def test_health_reports_llm_provider_when_structurer_is_llm():
    client, _ = _client(_settings(structurer="llm", llm_provider="fake"))
    body = client.get("/health").json()
    assert body["structuring_input"] == "natural_language"
    assert body["adapters"]["llm_provider"] == "fake"
    assert body["adapters"]["llm_model"]


# ---- 영상·클립 조회 ------------------------------------------------------------------


def test_version_exposes_playable_urls_and_media_types_after_generation():
    client, container = _client()
    draft = _create(client, container)
    assert draft["video_url"] is None and draft["video_media_type"] is None
    assert all(s["clip_url"] is None for s in draft["ir"]["scenes"])

    done = _generate(client, container, draft["version_id"])
    vid = done["version_id"]
    assert done["video_url"] == f"/api/versions/{vid}/video"
    assert done["video_media_type"] == "application/json"  # Fake 생성기는 영상 대신 JSON 매니페스트
    intro = next(s for s in done["ir"]["scenes"] if s["scene_key"] == "intro")
    assert intro["clip_url"] == f"/api/versions/{vid}/scenes/intro/clip"
    assert intro["clip_media_type"] == "application/json"

    video = client.get(done["video_url"])
    assert video.status_code == 200
    assert video.headers["content-type"].startswith("application/json")
    assert len(video.json()["clips"]) == 3
    clip = client.get(intro["clip_url"])
    assert clip.status_code == 200 and clip.json()["scene_key"] == "intro"


def test_media_errors_distinguish_not_ready_from_unknown():
    client, container = _client()
    draft = _create(client, container)
    vid = draft["version_id"]
    not_ready = client.get(f"/api/versions/{vid}/video")
    assert not_ready.status_code == 404 and not_ready.json()["error_code"] == "MEDIA_NOT_AVAILABLE"
    _generate(client, container, vid)
    unknown_scene = client.get(f"/api/versions/{vid}/scenes/ghost/clip")
    assert unknown_scene.status_code == 404 and unknown_scene.json()["error_code"] == "NOT_FOUND"
    unknown_version = client.get("/api/versions/ver_missing/video")
    assert (
        unknown_version.status_code == 404 and unknown_version.json()["error_code"] == "NOT_FOUND"
    )


class _Mp4Assembler(VideoAssembler):
    """실제 영상 파일을 흉내 낸다: 로컬 저장소에 video/mp4 로 저장."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def assemble(self, version_id: str, clips: Sequence[GeneratedClip]) -> VideoArtifact:
        uri = self._store.put(f"videos/{version_id}.mp4", b"0123456789", "video/mp4")
        return VideoArtifact(uri=uri, duration_ms=sum(c.duration_ms for c in clips))


def test_local_videos_are_served_with_range_support(tmp_path):
    store = LocalFileArtifactStore(tmp_path)
    client, container = _client(artifact_store=store, assembler=_Mp4Assembler(store))
    done = _generate(client, container, _create(client, container)["version_id"])
    assert done["video_media_type"] == "video/mp4"

    full = client.get(done["video_url"])
    assert full.status_code == 200 and full.content == b"0123456789"
    assert full.headers["content-type"] == "video/mp4"
    partial = client.get(done["video_url"], headers={"Range": "bytes=0-3"})
    assert partial.status_code == 206
    assert partial.content == b"0123"
    assert partial.headers["content-range"] == "bytes 0-3/10"


# ---- 장면 수준 조건 수정 ------------------------------------------------------------


def test_scene_level_conditions_can_be_revised():
    client, container = _client()
    draft = _create(client, container, prompt=SCENE_LEVEL_DSL)
    response = client.post(
        f"/api/versions/{draft['version_id']}/revisions",
        json={
            "entity_key": None,
            "predicate": "장소",
            "new_value": "Park",
            "scope": "ALL_APPEARANCES",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["changed_scene_keys"] == ["a", "b"]
    revised = client.get(f"/api/versions/{response.json()['version_id']}").json()
    locations = {
        s["scene_key"]: next(
            r["expected_value"] for r in s["requirements"] if r["predicate"] == "location"
        )
        for s in revised["ir"]["scenes"]
    }
    assert locations == {"a": "park", "b": "park"}


def test_scene_level_revision_rejects_relation_predicates():
    client, container = _client()
    draft = _create(client, container, prompt=SCENE_LEVEL_DSL)
    response = client.post(
        f"/api/versions/{draft['version_id']}/revisions",
        json={"predicate": "holds", "new_value": "cup"},
    )
    assert response.status_code == 422 and response.json()["error_code"] == "VALIDATION_ERROR"


# ---- 배포 옵션: CORS · 정적 서빙 · 요청 제한 · 입력 제한 ------------------------------


def test_cors_is_off_by_default_and_on_when_configured():
    off, _ = _client()
    preflight = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
    assert off.options("/api/projects", headers=preflight).status_code == 405
    on, _ = _client(_settings(cors_origins=("http://localhost:5173",)))
    response = on.options("/api/projects", headers=preflight)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_allows_every_method_the_api_uses():
    """감사 A-04: 다른 포트에서 띄운 화면도 API 가 쓰는 모든 메서드의 사전 요청을 통과한다
    (키 지우기 DELETE·IR 수정 PUT 등).

    메서드는 앱의 라우트에서 모은다. 다른 브랜치가 새 메서드의 라우트를 더해도 목록과 어긋나지 않는다 (델타 D09).
    """
    from fastapi.routing import APIRoute

    client, _ = _client(_settings(cors_origins=("http://localhost:5173",)))
    paths_by_method: dict[str, str] = {}
    for route in client.app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                paths_by_method.setdefault(method, route.path)
    assert {"GET", "POST", "PUT", "DELETE"} <= set(paths_by_method)

    for method, path in sorted(paths_by_method.items()):
        preflight = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": method}
        response = client.options(path, headers=preflight)
        assert response.status_code == 200, (method, path, response.text)
        assert method in response.headers["access-control-allow-methods"]


def test_built_frontend_can_be_served_from_the_same_origin(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>FRAME</title>", encoding="utf-8")
    client, _ = _client(_settings(frontend_dist=str(tmp_path)))
    page = client.get("/")
    assert page.status_code == 200 and "FRAME" in page.text
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/versions/ver_missing").json()["error_code"] == "NOT_FOUND"


def test_missing_frontend_dist_fails_fast(tmp_path):
    container = make_container(_settings(frontend_dist=str(tmp_path / "nope")))
    with pytest.raises(ConfigurationError):
        create_app(container)


def test_rate_limit_applies_to_mutating_requests_only():
    client, _ = _client(_settings(rate_limit_per_minute=2))
    body = {"title": "카페", "prompt": CAFE_PROMPT}
    assert [client.post("/api/projects", json=body).status_code for _ in range(2)] == [202, 202]
    limited = client.post("/api/projects", json=body)
    assert limited.status_code == 429
    assert limited.json()["error_code"] == "RATE_LIMITED"
    assert int(limited.headers["retry-after"]) >= 1
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"prompt": "x" * 4001},
        {"reference_images": [{"image_uri": f"mem://r/{i}.png"} for i in range(11)]},
        {"target_duration_ms": 300_001},
    ],
)
def test_input_limits_protect_the_llm_budget(overrides):
    client, _ = _client()
    body = {"title": "카페", "prompt": CAFE_PROMPT, **overrides}
    response = client.post("/api/projects", json=body)
    assert response.status_code == 422 and response.json()["error_code"] == "VALIDATION_ERROR"


def test_in_memory_store_reports_recorded_media_type():
    store = InMemoryArtifactStore()
    uri = store.put("videos/v.bin", b"x", "video/mp4")
    assert store.content_type(uri) == "video/mp4"
    assert store.local_path(uri) is None
