"""실제 보존 검수기처럼 detail.method 를 남기는 테스트용 검수기.

vlm_preservation(method=vlm_keep)·ssim_preservation(method=ssim) 을 함께 켠 수정 버전의 결과 모양을
클립을 읽지 않고 만든다: 부모 클립을 그대로 쓴 장면은 method=identical 1.0, 다시 만든 장면은 정한 점수.
"""

from __future__ import annotations

from collections.abc import Sequence

from frame.application.ports.checker import CheckContext, Checker, CheckOutcome
from frame.domain.enums import CheckType, Verdict


class MethodPreservationChecker(Checker):
    check_type = CheckType.PRESERVATION

    def __init__(self, method: str, regenerated_score: float, threshold: float = 0.9) -> None:
        self.method = method
        self.regenerated_score = regenerated_score
        self.threshold = threshold

    def check(self, ctx: CheckContext) -> Sequence[CheckOutcome]:
        if not ctx.is_revision:
            return []
        outcomes = []
        for scene in ctx.ir.scenes:
            if scene.scene_key in ctx.reused_scene_keys:
                score, detail = 1.0, {"reason": "clip_reused", "method": "identical"}
            else:
                score = self.regenerated_score
                detail = {"reason": "regenerated", "method": self.method}
            outcomes.append(
                CheckOutcome(
                    self.check_type,
                    Verdict.PASS if score >= self.threshold else Verdict.FAIL,
                    score=score,
                    scene_key=scene.scene_key,
                    detail=detail,
                )
            )
        return outcomes
