"""Relational ORM models mapped to the MidJab V3 final schema."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    resumes: Mapped[list["Resume"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="Master Resume")
    content_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_latex: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="resumes")
    applications: Mapped[list["Application"]] = relationship(back_populates="resume")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("idx_jobs_fingerprint", "fingerprint"),
        Index("idx_jobs_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    posted_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="NEW")

    applications: Mapped[list["Application"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        Index("idx_applications_status", "status"),
        Index("idx_applications_score", "match_score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING")
    match_score: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    score_reasoning: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tailored_content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generated_pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped["Job"] = relationship(back_populates="applications")
    resume: Mapped["Resume"] = relationship(back_populates="applications")
    logs: Mapped[list["PipelineLog"]] = relationship(back_populates="application", cascade="all, delete-orphan")


class PipelineLog(Base):
    __tablename__ = "pipeline_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), nullable=True, index=True
    )
    agent_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    application: Mapped["Application"] = relationship(back_populates="logs")

