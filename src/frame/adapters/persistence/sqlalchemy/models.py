"""ERD 와 대응하는 테이블. (확장 컬럼은 주석에 '확장' 으로 표시)

PROJECT, VIDEO_VERSION, SCENE, ENTITY, SCENE_ENTITY, REFERENCE_IMAGE, REQUIREMENT, CHECK_RESULT
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VersionRow(Base):
    __tablename__ = "video_versions"
    version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False)
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_versions.version_id"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    source_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)  # 확장
    target_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 확장
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 확장 (생성 조건)
    ir_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 확장 (IR 원본 문서)
    video_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    frozen_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 확장
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EntityRow(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("project_id", "entity_key"),)  # 무결성 규칙 2
    entity_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # 확장
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")  # 확장


class SceneRow(Base):
    __tablename__ = "scenes"
    __table_args__ = (UniqueConstraint("version_id", "scene_key"),)  # 무결성 규칙 2
    scene_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("video_versions.version_id", ondelete="CASCADE"), nullable=False
    )
    scene_key: Mapped[str] = mapped_column(String(64), nullable=False)
    planned_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")  # 확장
    clip_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)  # 확장 (장면 클립)
    clip_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 확장
    clip_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 확장


class SceneEntityRow(Base):
    __tablename__ = "scene_entities"
    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.scene_id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.entity_id"), primary_key=True)
    tracking_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    track_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    track_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 확장 (구간·박스)


class ReferenceImageRow(Base):
    __tablename__ = "reference_images"
    reference_image_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("video_versions.version_id", ondelete="CASCADE"), nullable=False
    )
    entity_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # 구조화 전엔 키만 보관
    image_uri: Mapped[str] = mapped_column(String(1024), nullable=False)


class RequirementRow(Base):
    __tablename__ = "requirements"
    requirement_id: Mapped[str] = mapped_column(String(320), primary_key=True)
    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.scene_id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.entity_id"), nullable=True)
    related_entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("entities.entity_id"), nullable=True
    )
    predicate: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_value: Mapped[str] = mapped_column(Text, nullable=False)


class CheckResultRow(Base):
    __tablename__ = "check_results"
    check_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("video_versions.version_id", ondelete="CASCADE"), nullable=False
    )
    requirement_id: Mapped[str | None] = mapped_column(
        ForeignKey("requirements.requirement_id"),
        nullable=True,  # 무결성 규칙 5 (FK)
    )
    check_type: Mapped[str] = mapped_column(String(16), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    entity_key: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 확장
    scene_key: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 확장
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # 확장
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # 확장
