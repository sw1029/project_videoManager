"""도메인/DTO -> 응답 스키마 변환 (Presentation 계층의 유일한 '로직')."""

from __future__ import annotations

from dataclasses import asdict

from frame.application.dto import ChecksView, EntityTracksView, ProjectDetail, VersionDetail
from frame.domain.identifiers import entity_id
from frame.domain.vocabulary import DEFAULT_VOCABULARY, PredicateVocabulary

from . import schemas as s


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


def version_out(detail: VersionDetail) -> s.VersionOut:
    v = detail.version
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
                    clip_uri=v.clips[sc.scene_key].uri if sc.scene_key in v.clips else None,
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
        error_code=v.error_code,
        error_message=v.error_message,
        reference_images=[
            s.ReferenceImageOut(
                reference_image_id=r.reference_image_id,
                image_uri=r.image_uri,
                entity_key=r.entity_key,
            )
            for r in detail.reference_images
        ],
        created_at=v.created_at,
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
