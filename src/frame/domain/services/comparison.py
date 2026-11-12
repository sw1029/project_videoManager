"""두 버전 비교 (US-04 AC-43 전후 비교, 대시보드의 '부모 대비 바뀐 장면 수').

규칙
- 장면 상태: 양쪽 클립이 같은 산출물(URI)이면 REUSED, 다르면 REGENERATED. 한쪽 IR 에만 있는 장면은 ADDED / REMOVED,
  양쪽에 있지만 한쪽이라도 클립이 없으면(생성 전·처리 중·실패) UNAVAILABLE.
  생성 단계는 부모 클립의 fingerprint 가 같으면 그 클립을 그대로 쓰므로, 부모와 비교할 때는
  fingerprint 일치와 URI 일치가 같다. 부모가 아닌 버전과 비교할 때 조건만 같고 따로 생성한 클립은
  바이트가 다를 수 있어 REGENERATED 로 둔다.
- 조건 변경(conditions_changed): 장면 조건과 등장 객체 정의의 해시(IR.scene_fingerprint)가 다르면 참.
  추가·삭제 장면도 참.
- 요구조건: 키로 짝을 짓는다. 값만 바뀌면 CHANGED, 관계 상대가 바뀌어 키가 달라진 조건은 (객체, 술어)가 같은 조건이
  양쪽에 하나씩일 때 CHANGED 로 묶는다. 나머지는 ADDED / REMOVED, 같으면 UNCHANGED.
- 순서: 대상(after) 버전의 순서를 따르고, 기준(before)에만 있는 것은 기준 순서대로 뒤에 붙인다.
- 지표 차이: after − before. 어느 한쪽이라도 None 이면 None.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from ..artifacts import GeneratedClip
from ..errors import InvalidStateTransition
from ..ir import IntermediateRepresentation, RequirementSpec, SceneSpec
from ..metrics import CheckSummary
from ..models import VideoVersion


class VersionNotComparable(InvalidStateTransition):
    """중간표현(IR)이 없는 버전(구조화 중·구조화 실패)을 비교하려 할 때 (API 409)."""

    code = "VERSION_NOT_COMPARABLE"


class SceneStatus(StrEnum):
    REUSED = "REUSED"  # 기준 버전의 클립을 그대로 쓴다 (같은 산출물)
    REGENERATED = "REGENERATED"  # 양쪽 클립이 다르다
    ADDED = "ADDED"  # 대상 버전에만 있는 장면
    REMOVED = "REMOVED"  # 기준 버전에만 있는 장면
    UNAVAILABLE = "UNAVAILABLE"  # 한쪽이라도 클립이 없다 (생성 전·처리 중·실패)


class RequirementChangeKind(StrEnum):
    UNCHANGED = "UNCHANGED"
    CHANGED = "CHANGED"
    ADDED = "ADDED"
    REMOVED = "REMOVED"


@dataclass(frozen=True)
class RequirementComparison:
    change: RequirementChangeKind
    before: RequirementSpec | None
    after: RequirementSpec | None

    def _spec(self) -> RequirementSpec:
        spec = self.after or self.before
        assert spec is not None
        return spec

    @property
    def entity_key(self) -> str | None:
        return self._spec().entity_key

    @property
    def predicate(self) -> str:
        return self._spec().predicate

    @property
    def is_relational(self) -> bool:
        return self._spec().is_relational


@dataclass(frozen=True)
class SceneComparison:
    scene_key: str
    status: SceneStatus
    conditions_changed: bool
    before_scene: SceneSpec | None
    after_scene: SceneSpec | None
    before_clip: GeneratedClip | None
    after_clip: GeneratedClip | None
    requirements: tuple[RequirementComparison, ...]

    @property
    def requirement_changes(self) -> tuple[RequirementComparison, ...]:
        return tuple(
            r for r in self.requirements if r.change is not RequirementChangeKind.UNCHANGED
        )


@dataclass(frozen=True)
class VersionComparison:
    scenes: tuple[SceneComparison, ...]

    @property
    def changed_scene_keys(self) -> tuple[str, ...]:
        return tuple(s.scene_key for s in self.scenes if s.conditions_changed)

    @property
    def requirement_change_count(self) -> int:
        return sum(len(s.requirement_changes) for s in self.scenes)

    def status_counts(self) -> dict[SceneStatus, int]:
        counts = Counter(s.status for s in self.scenes)
        return {status: counts.get(status, 0) for status in SceneStatus}


@dataclass(frozen=True)
class RateDelta:
    """검수 요약 세 지표의 차이 (after − before)."""

    requirement_reflection_rate: float | None
    consistency_rate: float | None
    preservation_rate: float | None


def ensure_comparable(version: VideoVersion) -> IntermediateRepresentation:
    if version.ir is None:
        raise VersionNotComparable(
            f"중간표현이 없는 버전 {version.version_id} 은 비교할 수 없습니다 (상태 {version.state})",
            version_id=version.version_id,
            state=version.state,
        )
    return version.ir


def compare_versions(before: VideoVersion, after: VideoVersion) -> VersionComparison:
    """before(기준) 대비 after(대상)를 장면별로 비교한다. 두 버전 모두 IR 이 있어야 한다."""
    before_ir, after_ir = ensure_comparable(before), ensure_comparable(after)
    after_keys = [s.scene_key for s in after_ir.scenes]
    keys = after_keys + [s.scene_key for s in before_ir.scenes if s.scene_key not in after_keys]
    scenes = []
    for key in keys:
        before_scene = before_ir.scene(key) if before_ir.has_scene(key) else None
        after_scene = after_ir.scene(key) if after_ir.has_scene(key) else None
        before_clip = before.clips.get(key) if before_scene is not None else None
        after_clip = after.clips.get(key) if after_scene is not None else None
        scenes.append(
            SceneComparison(
                scene_key=key,
                status=_scene_status(before_scene, after_scene, before_clip, after_clip),
                conditions_changed=(
                    before_scene is None
                    or after_scene is None
                    or before_ir.scene_fingerprint(key) != after_ir.scene_fingerprint(key)
                ),
                before_scene=before_scene,
                after_scene=after_scene,
                before_clip=before_clip,
                after_clip=after_clip,
                requirements=compare_requirements(before_scene, after_scene),
            )
        )
    return VersionComparison(scenes=tuple(scenes))


def compare_requirements(
    before: SceneSpec | None, after: SceneSpec | None
) -> tuple[RequirementComparison, ...]:
    """같은 장면의 요구조건을 비교한다. 장면이 한쪽에만 있으면 모두 ADDED 또는 REMOVED 다."""
    before_reqs = before.requirements if before is not None else ()
    after_reqs = after.requirements if after is not None else ()
    before_by_key = {r.key: r for r in before_reqs}
    after_keys = {r.key for r in after_reqs}
    unmatched_before = [r for r in before_reqs if r.key not in after_keys]
    unmatched_after = [r for r in after_reqs if r.key not in before_by_key]
    paired = _pair_relation_changes(unmatched_before, unmatched_after)

    rows: list[RequirementComparison] = []
    for req in after_reqs:
        previous = before_by_key.get(req.key) or paired.get(req.key)
        if previous is None:
            kind = RequirementChangeKind.ADDED
        elif previous == req:
            kind = RequirementChangeKind.UNCHANGED
        else:
            kind = RequirementChangeKind.CHANGED
        rows.append(RequirementComparison(kind, previous, req))
    paired_before_keys = {r.key for r in paired.values()}
    rows.extend(
        RequirementComparison(RequirementChangeKind.REMOVED, r, None)
        for r in unmatched_before
        if r.key not in paired_before_keys
    )
    return tuple(rows)


def rate_delta(before: CheckSummary, after: CheckSummary) -> RateDelta:
    return RateDelta(
        requirement_reflection_rate=_diff(
            before.requirement_reflection_rate, after.requirement_reflection_rate
        ),
        consistency_rate=_diff(before.consistency_rate, after.consistency_rate),
        preservation_rate=_diff(before.preservation_rate, after.preservation_rate),
    )


def _diff(before: float | None, after: float | None) -> float | None:
    return None if before is None or after is None else after - before


def _scene_status(
    before_scene: SceneSpec | None,
    after_scene: SceneSpec | None,
    before_clip: GeneratedClip | None,
    after_clip: GeneratedClip | None,
) -> SceneStatus:
    if before_scene is None:
        return SceneStatus.ADDED
    if after_scene is None:
        return SceneStatus.REMOVED
    if before_clip is None or after_clip is None:
        return SceneStatus.UNAVAILABLE
    return SceneStatus.REUSED if before_clip.uri == after_clip.uri else SceneStatus.REGENERATED


def _pair_relation_changes(
    removed: Sequence[RequirementSpec], added: Sequence[RequirementSpec]
) -> dict[str, RequirementSpec]:
    """키가 달라진 조건 중 (객체, 술어)가 같은 것이 양쪽에 하나씩이면 관계 상대만 바뀐 같은 조건으로 본다.

    반환: 대상(after) 요구조건 키 -> 짝이 된 기준(before) 요구조건.
    """

    def by_slot(
        reqs: Sequence[RequirementSpec],
    ) -> dict[tuple[str | None, str], list[RequirementSpec]]:
        groups: dict[tuple[str | None, str], list[RequirementSpec]] = {}
        for r in reqs:
            groups.setdefault((r.entity_key, r.predicate), []).append(r)
        return groups

    removed_by_slot = by_slot(removed)
    pairs: dict[str, RequirementSpec] = {}
    for slot, candidates in by_slot(added).items():
        previous = removed_by_slot.get(slot, [])
        if len(candidates) == 1 and len(previous) == 1:
            pairs[candidates[0].key] = previous[0]
    return pairs
