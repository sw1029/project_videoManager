"""API 로 본 전체 흐름: 정체성 기준 이미지 + 부모 클립 편집 + 실제 보존 검수 (ADR-0015, 0016).

실제 구성 요소를 그대로 쓴다: 환경 변수 설정 -> 컨테이너, OpenRouter 어댑터(urllib 전송),
ffmpeg 정규화·조립·프레임 추출, SQLite 파일, 로컬 산출물 폴더.
위쪽 모델만 루프백 가짜 서버다 (과금 없음). ffmpeg 가 없으면 건너뛴다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from frame.config import Settings, build_container
from frame.presentation.app import create_app
from tests.support.openrouter_server import FakeOpenRouterServer
from tests.support.video import find_ffmpeg

FFMPEG = find_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 없음 (video extra 미설치)")

# 카페 예시와 같은 구성, 장면만 2초 (빠르게 돌도록)
PROMPT = """
scene intro 0-2000 : 바리스타가 카운터에서 컵을 들어 보인다
scene closeup 2000-4000 : 컵 클로즈업
scene outro 4000-6000 : 바리스타가 손을 흔든다
entity barista PERSON : 흰 셔츠를 입은 바리스타
entity cup PROP : 세라믹 머그컵
in intro: barista, cup
in closeup: cup
in outro: barista
cup.color = red
barista.outfit = white shirt
barista holds -> cup @ intro
"""


@pytest.fixture
def upstream(tmp_path):
    server = FakeOpenRouterServer(FFMPEG, tmp_path)
    yield server
    server.close()


def _client(tmp_path, upstream, **env) -> TestClient:
    key_file = tmp_path / "open.txt"
    key_file.write_text("sk-or-v1-" + "f00d" * 16, encoding="utf-8")
    base = {
        "FRAME_GENERATOR": "external_api",
        "FRAME_ASSEMBLER": "ffmpeg",
        "FRAME_VIDEO_BASE_URL": upstream.base_url,
        "FRAME_VIDEO_KEY_FILE": str(key_file),
        "FRAME_VIDEO_RESOLUTION": "360p",
        "FRAME_VIDEO_POLL_INTERVAL_S": "0.01",
        "FRAME_CHECKERS": "fake_requirement,fake_consistency,ssim_preservation",
        "FRAME_PERSISTENCE": "sqlalchemy",
        "FRAME_DB_URL": f"sqlite+pysqlite:///{(tmp_path / 'frame.db').as_posix()}",
        "FRAME_ARTIFACT_STORE": "local_fs",
        "FRAME_ARTIFACT_DIR": str(tmp_path / "artifacts"),
        "FRAME_JOBS": "inline",
        "FRAME_IDENTITY": "frame",
    }
    base.update(env)
    return TestClient(create_app(build_container(Settings.from_env(base))))


def _generate(client, version_id):
    assert client.post(f"/api/versions/{version_id}/generations").status_code == 202
    version = client.get(f"/api/versions/{version_id}").json()
    assert version["state"] == "COMPLETED", version["error_message"]
    return version


def _revise(client, version_id, value="teal"):
    response = client.post(
        f"/api/versions/{version_id}/revisions",
        json={"entity_key": "cup", "predicate": "color", "new_value": value},
    )
    assert response.status_code == 201, response.text
    return response.json()["version_id"]


def _preservation(client, version_id):
    results = client.get(f"/api/versions/{version_id}/checks").json()["results"]
    return {r["scene_key"]: r for r in results if r["check_type"] == "PRESERVATION"}


@pytest.mark.ac("AC-01")
@pytest.mark.ac("AC-31")
def test_identity_anchors_and_parent_clip_editing_through_the_api(tmp_path, upstream):
    client = _client(tmp_path, upstream, FRAME_VIDEO_EDIT_MODE="video_reference")
    created = client.post("/api/projects", json={"title": "카페", "prompt": PROMPT, "seed": 7})
    original_id = created.json()["version_id"]

    original = _generate(client, original_id)

    # 1) 원본: intro 는 기준 없이, 뒤 장면은 intro 에서 뽑은 기준 이미지를 참조로 받는다
    intro, closeup, outro = upstream.submits
    assert upstream.image_references(intro) == []
    assert len(upstream.image_references(closeup)) == 1
    assert len(upstream.image_references(outro)) == 1
    assert upstream.image_references(outro)[0].startswith("data:image/jpeg;base64,")
    assert "- barista (person, Image 1)" in outro["prompt"]
    assert outro["prompt"].splitlines()[-1].startswith("Consistency:")
    anchors = [r for r in original["reference_images"] if r["origin"] == "DERIVED"]
    assert sorted((r["entity_key"], r["source_scene_key"]) for r in anchors) == [
        ("barista", "intro"),
        ("cup", "intro"),
    ]
    assert client.get(original["video_url"]).headers["content-type"] == "video/mp4"

    # 2) 수정(컵 색): 컵이 나오는 intro·closeup 만 부모 클립을 편집해서 만든다
    edited_id = _revise(client, original_id)
    _generate(client, edited_id)

    edit_intro, edit_closeup = upstream.submits[3:]
    for body in (edit_intro, edit_closeup):
        assert len(upstream.video_references(body)) == 1 and not upstream.image_references(body)
        assert body["prompt"].splitlines()[:2] == [
            "Edit the input video. Apply only these changes:",
            "- cup: color changes from red to teal",
        ]
    preserved = _preservation(client, edited_id)
    assert preserved["outro"]["detail"]["reason"] == "clip_reused"
    for scene_key in ("intro", "closeup"):  # 가짜 편집 모델은 화면을 그대로 돌려준다
        assert preserved[scene_key]["verdict"] == "PASS" and preserved[scene_key]["score"] > 0.95
    comparison = client.get(f"/api/versions/{edited_id}/comparison").json()
    assert comparison["summary"]["status_counts"]["REUSED"] == 1

    # 3) 같은 수정을 편집 없이: 장면을 새로 만들되 바리스타 기준은 이어 간다. 화면은 달라진다
    plain = _client(tmp_path, upstream)
    regenerated_id = _revise(plain, original_id)
    _generate(plain, regenerated_id)

    regen_intro, regen_closeup = upstream.submits[5:]
    assert not upstream.video_references(regen_intro)
    assert len(upstream.image_references(regen_intro)) == 1  # 부모 intro 에서 뽑은 바리스타
    assert "- barista (person, Image 1)" in regen_intro["prompt"]
    assert "cup (prop, Image" not in regen_intro["prompt"]  # 색이 바뀐 컵은 기준 없음
    assert len(upstream.image_references(regen_closeup)) == 1  # 새 intro 에서 뽑은 청록 컵
    changed = _preservation(plain, regenerated_id)
    assert changed["outro"]["score"] == 1.0
    assert changed["intro"]["verdict"] == "FAIL" and changed["intro"]["score"] < 0.9
    assert changed["intro"]["warnings"][0]["scene_key"] == "intro"

    # 4) previous_job_id 를 받지 않는 모델: 편집 요청은 거절되고, 3) 에서 만든 결과를 다시 쓴다 (또 생성하지 않음)
    continuing = _client(tmp_path, upstream, FRAME_VIDEO_EDIT_MODE="previous_job")
    fallback_id = _revise(continuing, original_id)
    fallback = _generate(continuing, fallback_id)

    attempts = upstream.submits[7:]
    assert len(attempts) == 2 and all("previous_job_id" in body for body in attempts)
    assert upstream.rejected == 2
    regenerated = plain.get(f"/api/versions/{regenerated_id}").json()
    clips = {s["scene_key"]: s["clip_uri"] for s in fallback["ir"]["scenes"]}
    assert clips == {s["scene_key"]: s["clip_uri"] for s in regenerated["ir"]["scenes"]}
