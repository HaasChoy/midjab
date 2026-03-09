"""Pydantic schemas aligned to MidJab V3 final SQL schema."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


# ─── User schemas ──────────────────────────────────────────────────────────────


class UserCreate(BaseModel):
    email: EmailStr
    name: str | None = None
    password: str = Field(min_length=8)


class UserRead(BaseModel):
    id: str
    email: EmailStr
    name: str | None = None
    email_verified: bool = False
    image: str | None = None
    created_at: datetime | None = None


# ─── Resume schemas ───────────────────────────────────────────────────────────


class ResumeCreate(BaseModel):
    user_id: str
    name: str = "Master Resume"
    content_json: dict
    raw_latex: str | None = None
    is_active: bool = True


class ResumeRead(BaseModel):
    id: str
    user_id: str | None = None
    name: str
    content_json: dict
    is_active: bool
    created_at: datetime | None = None


class ResumeUploadResponse(BaseModel):
    resume_id: str
    name: str
    content_json: dict
    message: str


# ─── Job schemas ──────────────────────────────────────────────────────────────


class JobCreate(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    company: str = Field(min_length=1, max_length=255)
    location: str | None = None
    description: str | None = None
    source: str | None = None
    source_url: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    posted_date: str | None = None
    status: str = "NEW"


# ─── Application schemas ─────────────────────────────────────────────────────


class ApplicationCreate(BaseModel):
    job_id: str
    resume_id: str | None = None
    status: str = "PENDING"
    match_score: float | None = None
    score_reasoning: dict | None = None
    tailored_content: dict | None = None
    generated_pdf_path: str | None = None


# ─── Pipeline schemas ────────────────────────────────────────────────────────


class PipelineLogCreate(BaseModel):
    application_id: str
    agent_name: str | None = None
    action: str | None = None
    message: str | None = None
    metadata: dict | None = None


class PipelineRunRequest(BaseModel):
    resume_id: str


class PipelineRunResponse(BaseModel):
    message: str
    resume_id: str
