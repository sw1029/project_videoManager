"""도메인/DTO -> 응답 스키마 변환 (Presentation 계층의 유일한 '로직')."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any
from urllib.parse import quote

from frame.application.dto import (
    ChecksView,
    EntityTracksView,
    ProjectDashboardView,
    ProjectDetail,
    VersionComparisonView,
    VersionDetail,
    VersionOverview,
)
from frame.application.ports.artifact_store import guess_media_type
from frame.application.ports.llm import LLMKeyStatus
from frame.application.use_cases.plan_revision import RevisionPlan
from frame.config.settings import Settings
from frame.domain.artifacts import GeneratedClip
from frame.domain.enums import ReferenceOrigin, Verdict
from frame.domain.identifiers import entity_id
from frame.domain.ir import RequirementSpec, SceneSpec
from frame.domain.progress import PipelineProgress
from frame.domain.revision import RevisionIntent
from frame.domain.services.comparison import RequirementComparison
from frame.domain.services.identity import is_valid_identity
from frame.domain.vocabulary import DEFAULT_VOCABULARY, PredicateVocabulary

from . import schemas as s


def llm_key_out(
    status: LLMKeyStatus, settings: Settings, *, entry_allowed: bool
) -> s.LLMKeyStatusOut:
    return s.LLMKeyStatusOut(
        configured=status.configured,
        source=status.source,  # type: ignore[arg-type]
        hint=status.hint,
        runtime_entry=entry_allowed,
        runtime_entry_enabled=settings.llm_runtime_key,
    )


def _value_kind(predicate: str) -> str | None:
    kind = DEFAULT_VOCABULARY.value_kind(predicate)
    return None if kind is None else str(kind)


def vocabulary_out(vocabulary: PredicateVocabulary = DEFAULT_VOCABULARY) -> s.VocabularyOut:
    return s.VocabularyOut(
        predicates=[
            s.PredicateOut(
                name=p.name,
                value_kind=str(p.value_kind),
                relational=p.relational,
                aliases=list(p.aliases),
                description=p.description,
            )
            for p in vocabulary.specs()
        ]
    )


def project_out(detail: ProjectDetail) -> s.ProjectOut:
    return s.ProjectOut(
        project_id=detail.project.project_id,
        title=detail.project.title,
        versions=[
            s.VersionSummaryOut(
                version_id=v.version_id,
                state=str(v.state),
                parent_version_id=v.parent_version_id,
                created_at=v.created_at,
            )
            for v in detail.versions
        ],
    )


def progress_out(progress: PipelineProgress | None) -> s.ProgressOut | None:
    if progress is None:
        return None
    return s.ProgressOut(
        stage=progress.current.stage if progress.current is not None else None,
        elapsed_ms=progress.elapsed_ms,
        stages=[s.StageProgressOut(**stage.to_dict()) for stage in progress.stages],
    )


def revision_out(intent: RevisionIntent | None) -> s.RevisionIntentOut | None:
    if intent is None:
        return None
    return s.RevisionIntentOut(
        kind=str(intent.kind),
        entity_key=intent.entity_key,
        predicate=intent.predicate,
        value=intent.value,
        previous_value=intent.previous_value,
        scope=str(intent.scope),
        target_scene_keys=list(intent.target_scene_keys),
        keep=[
            s.KeepItemOut(aspect=str(item.aspect), entity_key=item.entity_key)
            for item in intent.keep
        ],
        request_text=intent.request_text,
    )


def revision_plan_out(plan: RevisionPlan) -> s.RevisionPlanOut:
    change = plan.change
    regenerated = len(plan.target_scene_keys)
    return s.RevisionPlanOut(
        version_id=plan.version_id,
        kind=str(plan.kind),
        interpreted=plan.interpreted,
        notes=plan.notes,
        previous_value=plan.previous_value,
        target_scene_keys=list(plan.target_scene_keys),
        scenes=[
            s.RevisionPlanSceneOut(
                scene_key=scene.scene_key,
                planned_start_ms=scene.planned_start_ms,
                planned_end_ms=scene.planned_end_ms,
                will_regenerate=scene.will_regenerate,
                current_value=scene.current_value,
                verdict=str(scene.verdict) if scene.verdict is not None else None,
                recorded_cost_usd=scene.recorded_cost_usd,
                recorded_seconds=scene.recorded_seconds,
            )
            for scene in plan.scenes
        ],
        estimate=s.RevisionPlanEstimateOut(
            regenerated_scene_count=regenerated,
            reused_scene_count=len(plan.scenes) - regenerated,
            recorded_scene_count=plan.recorded_scene_count,
            cost_usd=plan.estimated_cost_usd,
            seconds=plan.estimated_seconds,
        ),
        revision_request=s.CreateRevisionIn(
            entity_key=change.entity_key,
            predicate=change.predicate,
            new_value=change.new_value,
            scope=str(change.scope),  # type: ignore[arg-type]
            scene_keys=list(change.scene_keys),
            current_value=change.current_value,
            kind=str(plan.kind),  # type: ignore[arg-type]
            keep=[
                s.KeepItemIn(aspect=str(item.aspect), entity_key=item.entity_key)  # type: ignore[arg-type]
                for item in plan.keep
            ],
            request_text=plan.request_text,
        ),
    )


def version_out(
    detail: VersionDetail,
    *,
    api_prefix: str = "/api",
    media_type_of: Callable[[str], str] = guess_media_type,
    is_stored: Callable[[str], bool] = lambda uri: False,
) -> s.VersionOut:
    """api_prefix·media_type_of 로 내부 URI 대신 재생 가능한 HTTP 경로와 미디어 형식을 채운다.

    is_stored: 참조 이미지가 산출물 저장소에 있는지. 있으면 image_url 로 내려받을 경로를 준다.
    """
    v = detail.version
    base = f"{api_prefix}/versions/{quote(v.version_id, safe='')}"

    def clip_fields(scene_key: str) -> dict[str, str | None]:
        clip = v.clips.get(scene_key)
        if clip is None:
            return {"clip_uri": None, "clip_url": None, "clip_media_type": None}
        return {
            "clip_uri": clip.uri,
            "clip_url": f"{base}/scenes/{quote(scene_key, safe='')}/clip",
            "clip_media_type": media_type_of(clip.uri),
        }

    ir_out = None
    if v.ir is not None:
        ir_out = s.IROut(
            entities=[
                s.EntityOut(
                    entity_id=entity_id(v.project_id, e.entity_key),
                    entity_key=e.entity_key,
                    kind=str(e.kind),
                    description=e.description,
                )
                for e in v.ir.entities
            ],
            scenes=[
                s.SceneOut(
                    scene_key=sc.scene_key,
                    planned_start_ms=sc.planned_start_ms,
                    planned_end_ms=sc.planned_end_ms,
                    description=sc.description,
                    entity_keys=list(sc.entity_keys),
                    requirements=[
                        s.RequirementOut(
                            requirement_key=r.key,
                            scene_key=r.scene_key,
                            entity_key=r.entity_key,
                            related_entity_key=r.related_entity_key,
                            predicate=r.predicate,
                            expected_value=r.expected_value,
                            value_kind=_value_kind(r.predicate),
                            relational=r.is_relational,
                        )
                        for r in sc.requirements
                    ],
                    **clip_fields(sc.scene_key),
                )
                for sc in v.ir.scenes
            ],
        )
    return s.VersionOut(
        version_id=v.version_id,
        project_id=v.project_id,
        parent_version_id=v.parent_version_id,
        state=str(v.state),
        source_prompt=v.source_prompt,
        seed=v.seed,
        frozen_fingerprint=v.frozen_fingerprint,
        ir=ir_out,
        video_uri=v.video_uri,
        video_url=f"{base}/video" if v.video_uri else None,
        video_media_type=media_type_of(v.video_uri) if v.video_uri else None,
        error_code=v.error_code,
        error_message=v.error_message,
        reference_images=[
            s.ReferenceImageOut(
                reference_image_id=r.reference_image_id,
                image_uri=r.image_uri,
                image_url=(
                    f"{base}/reference-images/{quote(r.reference_image_id, safe='')}"
                    if is_stored(r.image_uri)
                    else None
                ),
                entity_key=r.entity_key,
                origin=str(r.origin),  # type: ignore[arg-type]
                source_scene_key=r.source_scene_key,
                description=r.description or None,
                is_current=(
                    r.origin is not ReferenceOrigin.DERIVED
                    or v.ir is None
                    or is_valid_identity(v.ir, r)
                ),
            )
            for r in detail.reference_images
        ],
        created_at=v.created_at,
        progress=progress_out(v.progress),
        revision=revision_out(v.intent),
    )


def checks_out(view: ChecksView) -> s.ChecksOut:
    return s.ChecksOut(
        version_id=view.version_id,
        state=str(view.state),
        summary=s.CheckSummaryOut(**asdict(view.summary)),
        results=[
            s.CheckResultOut(
                check_id=r.check_id,
                check_type=str(r.check_type),
                verdict=str(r.verdict),
                score=r.score,
                requirement_key=r.requirement_key,
                entity_key=r.entity_key,
                scene_key=r.scene_key,
                warnings=[s.WarningOut(**asdict(w)) for w in r.warnings],
                detail=dict(r.detail),
            )
            for r in view.results
        ],
    )


def tracks_out(view: EntityTracksView) -> s.EntityTracksOut:
    return s.EntityTracksOut(
        version_id=view.version_id,
        entity_key=view.entity_key,
        records=[
            s.TrackRecordOut(
                scene_key=t.scene_key,
                entity_key=t.entity_key,
                status=str(t.status),
                track_uri=t.track_uri,
                segments=[
                    s.TrackSegmentOut(
                        start_ms=seg.start_ms,
                        end_ms=seg.end_ms,
                        boxes=[s.BoxOut(**asdict(b)) for b in seg.boxes],
                    )
                    for seg in t.segments
                ],
            )
            for t in view.records
        ],
    )


# ---- US-04 전후 비교·대시보드 (ADR-0014) ----------------------------------------------

MediaTypeOf = Callable[[str], str]


def _version_path(api_prefix: str, version_id: str) -> str:
    return f"{api_prefix}/versions/{quote(version_id, safe='')}"


def _overview_fields(
    overview: VersionOverview, api_prefix: str, media_type_of: MediaTypeOf
) -> dict[str, Any]:
    v = overview.version
    return {
        "version_id": v.version_id,
        "parent_version_id": v.parent_version_id,
        "state": str(v.state),
        "error_code": v.error_code,
        "error_message": v.error_message,
        "created_at": v.created_at,
        "video_url": f"{_version_path(api_prefix, v.version_id)}/video" if v.video_uri else None,
        "video_media_type": media_type_of(v.video_uri) if v.video_uri else None,
        "checks": s.CheckSummaryOut(**asdict(overview.summary)),
        "revision": revision_out(v.intent),
    }


def _compared_scene(
    version_id: str,
    scene: SceneSpec | None,
    clip: GeneratedClip | None,
    api_prefix: str,
    media_type_of: MediaTypeOf,
) -> s.ComparedSceneOut | None:
    if scene is None:
        return None
    clip_url = None
    if clip is not None:
        scene_path = quote(scene.scene_key, safe="")
        clip_url = f"{_version_path(api_prefix, version_id)}/scenes/{scene_path}/clip"
    return s.ComparedSceneOut(
        planned_start_ms=scene.planned_start_ms,
        planned_end_ms=scene.planned_end_ms,
        description=scene.description,
        clip_url=clip_url,
        clip_media_type=media_type_of(clip.uri) if clip is not None else None,
    )


def _verdict(verdicts: Mapping[str, Verdict], requirement: RequirementSpec | None) -> str | None:
    verdict = verdicts.get(requirement.key) if requirement is not None else None
    return None if verdict is None else str(verdict)


def _requirement_comparison(
    row: RequirementComparison,
    before_verdicts: Mapping[str, Verdict],
    after_verdicts: Mapping[str, Verdict],
) -> s.RequirementComparisonOut:
    before, after = row.before, row.after
    return s.RequirementComparisonOut(
        change=str(row.change),
        entity_key=row.entity_key,
        predicate=row.predicate,
        value_kind=_value_kind(row.predicate),
        relational=row.is_relational,
        before_requirement_key=before.key if before is not None else None,
        after_requirement_key=after.key if after is not None else None,
        before_value=before.expected_value if before is not None else None,
        after_value=after.expected_value if after is not None else None,
        before_verdict=_verdict(before_verdicts, before),
        after_verdict=_verdict(after_verdicts, after),
    )


def comparison_out(
    view: VersionComparisonView,
    *,
    api_prefix: str = "/api",
    media_type_of: MediaTypeOf = guess_media_type,
) -> s.VersionComparisonOut:
    before_id = view.before.version.version_id
    after_id = view.after.version.version_id
    result = view.comparison
    return s.VersionComparisonOut(
        version_id=after_id,
        against_version_id=before_id,
        against_is_parent=view.against_is_parent,
        before=s.VersionOverviewOut(**_overview_fields(view.before, api_prefix, media_type_of)),
        after=s.VersionOverviewOut(**_overview_fields(view.after, api_prefix, media_type_of)),
        checks_delta=s.CheckDeltaOut(**asdict(view.checks_delta)),
        summary=s.ComparisonSummaryOut(
            scene_count=len(result.scenes),
            status_counts={str(status): n for status, n in result.status_counts().items()},
            changed_scene_keys=list(result.changed_scene_keys),
            requirement_change_count=result.requirement_change_count,
        ),
        scenes=[
            s.SceneComparisonOut(
                scene_key=sc.scene_key,
                status=str(sc.status),
                conditions_changed=sc.conditions_changed,
                before=_compared_scene(
                    before_id, sc.before_scene, sc.before_clip, api_prefix, media_type_of
                ),
                after=_compared_scene(
                    after_id, sc.after_scene, sc.after_clip, api_prefix, media_type_of
                ),
                requirements=[
                    _requirement_comparison(
                        r, view.before.requirement_verdicts, view.after.requirement_verdicts
                    )
                    for r in sc.requirements
                ],
            )
            for sc in result.scenes
        ],
    )


def dashboard_out(
    view: ProjectDashboardView,
    *,
    api_prefix: str = "/api",
    media_type_of: MediaTypeOf = guess_media_type,
) -> s.ProjectDashboardOut:
    return s.ProjectDashboardOut(
        project_id=view.project.project_id,
        title=view.project.title,
        created_at=view.project.created_at,
        summary=s.DashboardSummaryOut(
            version_count=len(view.versions),
            state_counts={str(state): n for state, n in view.state_counts.items()},
            latest_version_id=view.latest_version_id,
            latest_completed_version_id=view.latest_completed_version_id,
        ),
        versions=[
            s.DashboardVersionOut(
                **_overview_fields(node.overview, api_prefix, media_type_of),
                depth=node.depth,
                child_version_ids=list(node.child_version_ids),
                scene_count=node.scene_count,
                changed_scene_count=node.changed_scene_count,
            )
            for node in view.versions
        ],
    )
