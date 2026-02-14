"""Pydantic schemas aligned to MidJab V3 final SQL schema."""

from __future__ import annotations

import uuid
from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    name: str | None = None
    password_hash: str | None = Field(default=None, min_length=8)


class UserRead(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str | None = None


class ResumeCreate(BaseModel):
    user_id: uuid.UUID
    name: str = "Master Resume"
    content_json: dict
    raw_latex: str | None = None
    is_active: bool = True


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


class ApplicationCreate(BaseModel):
    job_id: uuid.UUID
    resume_id: uuid.UUID | None = None
    status: str = "PENDING"
    match_score: float | None = None
    score_reasoning: dict | None = None
    tailored_content: dict | None = None
    generated_pdf_path: str | None = None


class PipelineLogCreate(BaseModel):
    application_id: uuid.UUID
    agent_name: str | None = None
    action: str | None = None
    message: str | None = None
    metadata: dict | None = None

