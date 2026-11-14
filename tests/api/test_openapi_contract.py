"""저장된 OpenAPI 명세(docs/openapi.json)와 현재 앱의 필드 구조가 같은지 확인한다 (프론트 계약 보호)."""

import json
from pathlib import Path

from frame.tools.export_openapi import build_spec, contract_signature

SNAPSHOT = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
HINT = "python -m frame.tools.export_openapi 로 docs/openapi.json 을 갱신하고 PR 에 API 변경을 적으세요"


def test_committed_openapi_matches_the_running_app():
    assert SNAPSHOT.is_file(), HINT
    committed = contract_signature(json.loads(SNAPSHOT.read_text(encoding="utf-8")))
    current = contract_signature(build_spec())
    added, removed = sorted(current - committed), sorted(committed - current)
    assert not added and not removed, (
        f"API 계약 변경: 추가={added[:10]} 삭제={removed[:10]}. {HINT}"
    )


def test_signature_covers_the_fields_the_frontend_reads():
    signature = contract_signature(build_spec())
    for entry in (
        "POST /api/projects -> 202:version_id",
        "GET /api/versions/{version_id} -> 200:ir.scenes[].requirements[].expected_value",
        "GET /api/versions/{version_id} -> 200:video_url",
        "GET /api/versions/{version_id}/checks -> 200:results[].warnings[].message",
        "POST /api/versions/{version_id}/revisions body:current_value",
        "GET /health -> 200:structuring_input",
        "GET /api/versions/{version_id}/scenes/{scene_key}/clip param:scene_key",
        # 블록 입력·구조화 결과 수정·참조 이미지 업로드 (#10 #11 #12)
        "POST /api/projects body:ir.scenes[].requirements[].predicate",
        "POST /api/projects -> 201:repair_notes[]",
        "PUT /api/versions/{version_id}/ir body:entities[].display_name",
        "PUT /api/versions/{version_id}/ir -> 200:repair_notes[]",
        "GET /api/versions/{version_id} -> 200:ir.entities[].display_name",
        "POST /api/reference-images body:file",
        "POST /api/reference-images -> 201:image_uri",
    ):
        assert entry in signature, entry
