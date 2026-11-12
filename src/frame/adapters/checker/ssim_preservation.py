"""SSIM 기반 보존 검수기 (FRAME_CHECKERS 의 ssim_preservation, US-01 AC-01, ADR-0016).

수정 버전의 장면이 부모 클립을 얼마나 그대로 두었는지를 잰다.
- 부모 클립을 재사용한 장면: 정의상 1.0 (바이트가 같다).
- 다시 만든 장면: 부모 클립과 자식 클립의 프레임별 SSIM 평균 (ffmpeg ssim 필터, 0..1). 임계값(0.9) 이상이면 PASS.
  change_rate = 1 − SSIM 은 '지정하지 않은 대상의 변경률' 의 상한이다. 화면 전체를 비교하므로 일부러 바꾼
  대상(예: 컵 색)의 변화도 들어 있다. 대상 영역을 빼려면 추적 박스·마스크가 필요하다 (AC-34, 실제 추적기 뒤).
- 비교할 수 없으면 UNDETERMINED: 부모에 그 장면 클립이 없음, 클립이 영상이 아님, ffmpeg 실패.

장면을 통째로 다시 생성하면 seed 가 같아도 구도·배경이 달라져 이 값이 크게 떨어진다. 부모 클립을 편집하는
생성기(ADR-0016)가 그 차이를 줄이는지 이 검수기로 본다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from frame.adapters.video_tools import require_ffmpeg, ssim
from frame.application.errors import AdapterError
from frame.application.ports.artifact_store import ArtifactStore
from frame.application.ports.checker import CheckContext, Checker, CheckOutcome
from frame.domain.checks import WarningInterval
from frame.domain.enums import CheckType, Verdict
from frame.domain.ir import SceneSpec

log = logging.getLogger(__name__)


class SsimPreservationChecker(Checker):
    check_type = CheckType.PRESERVATION

    def __init__(self, store: ArtifactStore, ffmpeg: str | None, *, threshold: float = 0.9) -> None:
        self._store = store
        self._ffmpeg = require_ffmpeg(ffmpeg)
        self.threshold = threshold

    def check(self, ctx: CheckContext) -> Sequence[CheckOutcome]:
        if not ctx.is_revision:
            return []
        return [self._check_scene(ctx, scene) for scene in ctx.ir.scenes]

    def _check_scene(self, ctx: CheckContext, scene: SceneSpec) -> CheckOutcome:
        key = scene.scene_key
        if key in ctx.reused_scene_keys:
            return CheckOutcome(
                self.check_type,
                Verdict.PASS,
                score=1.0,
                scene_key=key,
                detail={"reason": "clip_reused", "method": "identical"},
            )
        clip, parent = ctx.clips.get(key), ctx.parent_clips.get(key)
        if clip is None or parent is None:
            return self._undetermined(key, "부모 버전에 비교할 장면 클립이 없음")
        for uri in (clip.uri, parent.uri):
            if not self._store.content_type(uri).startswith("video/"):
                return self._undetermined(key, f"영상이 아닌 클립은 비교할 수 없음: {uri}")
        try:
            score = ssim(self._ffmpeg, self._store.get(parent.uri), self._store.get(clip.uri))
        except AdapterError as exc:
            log.warning("보존 검수 미판정 (장면 %s): %s: %s", key, exc.code, exc)
            return self._undetermined(key, f"{exc.code}: {exc}")
        passed = score >= self.threshold
        warnings = ()
        if not passed:
            warnings = (
                WarningInterval(
                    scene_key=key,
                    start_ms=scene.planned_start_ms,
                    end_ms=scene.planned_end_ms,
                    message=f"부모 클립과 화면이 많이 달라졌습니다 (SSIM {score:.2f})",
                ),
            )
        return CheckOutcome(
            self.check_type,
            Verdict.PASS if passed else Verdict.FAIL,
            score=score,
            scene_key=key,
            warnings=warnings,
            detail={
                "reason": "regenerated",
                "method": "ssim",
                "ssim": score,
                "change_rate": 1.0 - score,
                "threshold": self.threshold,
                "parent_clip_uri": parent.uri,
            },
        )

    def _undetermined(self, scene_key: str, reason: str) -> CheckOutcome:
        return CheckOutcome(
            self.check_type,
            Verdict.UNDETERMINED,
            scene_key=scene_key,
            detail={"reason": reason[:400], "method": "ssim"},
        )
