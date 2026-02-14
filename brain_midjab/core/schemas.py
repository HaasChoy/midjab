"""
Pydantic schemas for MidJab V3 relational entities.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password_hash: str = Field(min_length=8)


class UserRead(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    created_at: datetime


class ProfileCreate(BaseModel):
    user_id: uuid.UUID
    raw_tex_content: dict[str, Any] | None = None
    parsed_json_v: dict[str, Any] | None = None
    fingerprint: str = Field(min_length=64, max_length=64)


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    website: str | None = None
    industry: str | None = None


class JobPostingCreate(BaseModel):
    company_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    source_platform: str | None = None
    fingerprint: str = Field(min_length=64, max_length=64)
    salary_min: float | None = None
    salary_max: float | None = None


class JobScoreCreate(BaseModel):
    job_id: uuid.UUID
    profile_id: uuid.UUID
    total_score: float | None = None
    skill_score: float | None = None
    semantic_score: float | None = None


class TailoredResumeCreate(BaseModel):
    job_id: uuid.UUID
    profile_id: uuid.UUID
    tailored_tex: str | None = None
    status: str = "drafting"

