"""
Relational ORM models for MidJab V3.

These map the new SQL schema:
users, profiles, companies, job_postings, job_scores, tailored_resumes.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.database import Base


class JobPostingStatus(str, enum.Enum):
    active = "active"
    closed = "closed"


class TailoredResumeStatus(str, enum.Enum):
    drafting = "drafting"
    ready = "ready"
    compiled = "compiled"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    profiles: Mapped[list["Profile"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Profile(Base):
    __tablename__ = "profiles"

    profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_tex_content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    parsed_json_v: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="profiles")
    scores: Mapped[list["JobScore"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    tailored_resumes: Mapped[list["TailoredResume"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class Company(Base):
    __tablename__ = "companies"

    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(150), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    job_postings: Mapped[list["JobPosting"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_job_postings_fingerprint"),
        Index("ix_job_postings_status", "status"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.company_id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_platform: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[JobPostingStatus] = mapped_column(
        Enum(JobPostingStatus, name="job_posting_status"), default=JobPostingStatus.active, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    company: Mapped["Company"] = relationship(back_populates="job_postings")
    scores: Mapped[list["JobScore"]] = relationship(back_populates="job_posting", cascade="all, delete-orphan")
    tailored_resumes: Mapped[list["TailoredResume"]] = relationship(
        back_populates="job_posting", cascade="all, delete-orphan"
    )


class JobScore(Base):
    __tablename__ = "job_scores"
    __table_args__ = (
        Index("ix_job_scores_total_score", "total_score"),
        UniqueConstraint("job_id", "profile_id", name="uq_job_scores_job_profile"),
    )

    score_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.job_id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.profile_id", ondelete="CASCADE"), nullable=False, index=True
    )
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    skill_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    semantic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    job_posting: Mapped["JobPosting"] = relationship(back_populates="scores")
    profile: Mapped["Profile"] = relationship(back_populates="scores")


class TailoredResume(Base):
    __tablename__ = "tailored_resumes"
    __table_args__ = (
        UniqueConstraint("job_id", "profile_id", name="uq_tailored_resumes_job_profile"),
        Index("ix_tailored_resumes_status", "status"),
    )

    resume_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.job_id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.profile_id", ondelete="CASCADE"), nullable=False, index=True
    )
    tailored_tex: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TailoredResumeStatus] = mapped_column(
        Enum(TailoredResumeStatus, name="tailored_resume_status"),
        default=TailoredResumeStatus.drafting,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    job_posting: Mapped["JobPosting"] = relationship(back_populates="tailored_resumes")
    profile: Mapped["Profile"] = relationship(back_populates="tailored_resumes")

