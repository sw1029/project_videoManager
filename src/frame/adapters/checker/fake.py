"""테스트용 검수 어댑터 3종 (CheckType 별 Strategy).

실제 구현을 붙일 때도 같은 규약을 지킨다:
- 판정 불가(추적 실패 등)는 FAIL 이 아니라 UNDETERMINED.
- score 는 0..1 (지표 집계에 바로 쓰인다).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from frame.application.ports.checker import CheckContext, Checker, CheckOutcome
from frame.domain.checks import WarningInterval
from frame.domain.enums import CheckType, TrackingStatus, Verdict
from frame.domain.tracking import TrackRecord


class FakeRequirementChecker(Checker):
    """요구조건별 반영 여부. 대상 객체의 추적이 실패한 장면은 UNDETERMINED."""

    check_type = CheckType.REQUIREMENT

    def __init__(
        self, failing_keys: Iterable[str] = (), undetermined_keys: Iterable[str] = ()
    ) -> None:
        self._failing = set(failing_keys)
        self._undetermined = set(undetermined_keys)

    def check(self, ctx: CheckContext) -> Sequence[CheckOutcome]:
        failed_tracks = {
            (t.scene_key, t.entity_key) for t in ctx.tracks if t.status is TrackingStatus.FAILED
        }
        outcomes = []
        for req in ctx.ir.iter_requirements():
            if req.key in self._undetermined or (req.scene_key, req.entity_key) in failed_tracks:
                verdict, score = Verdict.UNDETERMINED, None
            elif req.key in self._failing:
                verdict, score = Verdict.FAIL, 0.0
            else:
                verdict, score = Verdict.PASS, 1.0
            outcomes.append(
                CheckOutcome(
                    check_type=self.check_type,
                    verdict=verdict,
                    score=score,
                    requirement_key=req.key,
                    entity_key=req.entity_key,
                    scene_key=req.scene_key,
                    detail={"predicate": req.predicate, "expected_value": req.expected_value},
                )
            )
        return outcomes


class FakeConsistencyChecker(Checker):
    """객체별 타임라인 일관성. score = 1 - (경고 구간 길이 / 등장 구간 길이)."""

    check_type = CheckType.CONSISTENCY

    def __init__(
        self,
        warnings_by_entity: Mapping[str, Sequence[WarningInterval]] | None = None,
        threshold: float = 0.9,
    ) -> None:
        self._warnings = {k: tuple(v) for k, v in (warnings_by_entity or {}).items()}
        self._threshold = threshold

    def check(self, ctx: CheckContext) -> Sequence[CheckOutcome]:
        outcomes = []
        for entity in ctx.ir.entities:
            tracks: list[TrackRecord] = [t for t in ctx.tracks if t.entity_key == entity.entity_key]
            if not tracks:
                continue
            if any(t.status is not TrackingStatus.COMPLETED for t in tracks):
                outcomes.append(
                    CheckOutcome(
                        self.check_type,
                        Verdict.UNDETERMINED,
                        entity_key=entity.entity_key,
                        detail={"reason": "추적 실패 구간이 있어 판정할 수 없음"},
                    )
                )
                continue
            appearance_ms = sum(s.end_ms - s.start_ms for t in tracks for s in t.segments)
            warnings = self._warnings.get(entity.entity_key, ())
            warned_ms = sum(w.end_ms - w.start_ms for w in warnings)
            score = 1.0 if appearance_ms == 0 else max(0.0, 1.0 - warned_ms / appearance_ms)
            outcomes.append(
                CheckOutcome(
                    self.check_type,
                    Verdict.PASS if score >= self._threshold else Verdict.FAIL,
                    score=score,
                    entity_key=entity.entity_key,
                    warnings=tuple(warnings),
                    detail={"appearance_ms": appearance_ms, "warned_ms": warned_ms},
                )
            )
        return outcomes


class FakePreservationChecker(Checker):
    """수정 버전에서 장면별 비대상 보존. 재사용한 장면은 정의상 1.0, 재생성한 장면은 설정값."""

    check_type = CheckType.PRESERVATION

    def __init__(self, regenerated_scene_score: float = 0.95, threshold: float = 0.9) -> None:
        self._regenerated_score = regenerated_scene_score
        self._threshold = threshold

    def check(self, ctx: CheckContext) -> Sequence[CheckOutcome]:
        if not ctx.is_revision:
            return []
        outcomes = []
        for scene in ctx.ir.scenes:
            if scene.scene_key in ctx.reused_scene_keys:
                score, reason = 1.0, "clip_reused"
            else:
                score, reason = self._regenerated_score, "regenerated"
            outcomes.append(
                CheckOutcome(
                    self.check_type,
                    Verdict.PASS if score >= self._threshold else Verdict.FAIL,
                    score=score,
                    scene_key=scene.scene_key,
                    detail={"reason": reason},
                )
            )
        return outcomes
