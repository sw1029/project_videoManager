"""장면 응답의 clip_production: 이 클립이 새로 생성됐는지, 부모 클립을 편집해 만들어졌는지 (ADR-0016·0019).

2차 실호출 3회차에서 수정이 편집 대신 새로 생성으로 처리됐는데, API 응답으로는 그 사실을 알 수 없었다.
"""

from __future__ import annotations

from frame.domain.artifacts import GeneratedClip
from frame.presentation.mappers import clip_production_out


def _clip(metadata):
    return GeneratedClip("show_cup", "local://clips/ver_1/show_cup.mp4", 4000, "fp", metadata)


def test_an_edited_clip_reports_the_edit_model_cost_and_time():
    production = clip_production_out(
        _clip(
            {
                "generator": "openrouter",
                "model": "black-forest-labs/flux-video-edit",
                "cost_usd": 0.12,
                "generation_seconds": 41.359,
                "edit": {"mode": "video_reference", "applied": True, "parent_url_lookups": 2},
            }
        )
    )

    assert production is not None
    assert production.model_dump() == {
        "method": "EDITED",
        "model": "black-forest-labs/flux-video-edit",
        "cost_usd": 0.12,
        "seconds": 41.359,
        "edit_fallback_reason": None,
        "provisional": False,
    }


def test_a_clip_regenerated_because_the_edit_was_not_possible_says_why():
    reason = "부모 작업의 저장본 주소를 얻지 못함 (6회 조회: 없거나 만료됐거나 아직 나오지 않음)"
    production = clip_production_out(
        _clip(
            {
                "model": "alibaba/wan-3.0",
                "cost_usd": 0.2125,
                "generation_seconds": 144.0,
                "edit": {"mode": "video_reference", "applied": False, "fallback_reason": reason},
            }
        )
    )

    assert production is not None
    assert (production.method, production.edit_fallback_reason) == ("GENERATED", reason)
    assert (production.model, production.cost_usd, production.seconds) == (
        "alibaba/wan-3.0",
        0.2125,
        144.0,
    )


def test_a_plainly_generated_clip_has_no_fallback_reason():
    production = clip_production_out(_clip({"generator": "fake"}))

    assert production is not None
    assert production.model_dump() == {
        "method": "GENERATED",
        "model": None,
        "cost_usd": None,
        "seconds": None,
        "edit_fallback_reason": None,
        "provisional": False,
    }


def test_a_clip_without_a_record_has_no_production():
    assert clip_production_out(_clip({})) is None


def test_a_provisional_regeneration_is_flagged_so_the_screen_can_offer_a_retry():
    """일시적인 사정(주소가 아직 없음)으로 새로 생성한 결과다 (ADR-0021).

    같은 수정을 다시 요청하면 편집으로 만들 수 있다.
    """
    production = clip_production_out(
        _clip(
            {
                "model": "alibaba/wan-3.0",
                "cost_usd": 0.2125,
                "generation_seconds": 144.0,
                "provisional": True,
                "edit": {
                    "applied": False,
                    "transient": True,
                    "fallback_reason": "아직 나오지 않음",
                },
            }
        )
    )

    assert production is not None
    assert (production.method, production.provisional) == ("GENERATED", True)
