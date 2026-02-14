#!/usr/bin/env python3
"""
One-time migration utility from MidJab V2 MongoDB to MidJab V3 PostgreSQL.

This script is intentionally conservative:
- Skips invalid rows instead of hard-failing
- Uses deterministic mappings by fingerprint where possible
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from pymongo import MongoClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.database import SessionLocal
from core.orm_models import Company, JobPosting, JobPostingStatus, JobScore, Profile, TailoredResume, TailoredResumeStatus, User

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI_OLD", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB_OLD", "midjab_v2")
DEFAULT_MIGRATION_EMAIL = os.getenv("MIGRATION_USER_EMAIL", "migrated-user@midjab.local")


def _mongo() -> Any:
    return MongoClient(MONGO_URI)[MONGO_DB]


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _status_to_job(status: str | None) -> JobPostingStatus:
    if (status or "").lower() == "closed":
        return JobPostingStatus.closed
    return JobPostingStatus.active


def _status_to_resume(status: str | None) -> TailoredResumeStatus:
    mapping = {
        "pending_draft": TailoredResumeStatus.drafting,
        "drafting": TailoredResumeStatus.drafting,
        "ready_to_compile": TailoredResumeStatus.ready,
        "compiling": TailoredResumeStatus.ready,
        "completed": TailoredResumeStatus.compiled,
        "failed": TailoredResumeStatus.failed,
    }
    return mapping.get((status or "").lower(), TailoredResumeStatus.drafting)


def _get_or_create_migration_user(db: Session) -> User:
    user = db.query(User).filter(User.email == DEFAULT_MIGRATION_EMAIL).first()
    if user:
        return user
    user = User(email=DEFAULT_MIGRATION_EMAIL, password_hash="MIGRATED_ACCOUNT", created_at=datetime.utcnow())
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def migrate() -> None:
    mongo = _mongo()
    db = SessionLocal()

    company_by_name: dict[str, uuid.UUID] = {}
    job_by_fingerprint: dict[str, uuid.UUID] = {}

    try:
        user = _get_or_create_migration_user(db)

        # Build single profile from existing outputs/user_profile.json if available in Mongo context.
        profile_payload = mongo.get_collection("user_profile").find_one() or {}
        if not profile_payload:
            profile_payload = {"source": "migration"}

        profile_fingerprint = hashlib.sha256(str(profile_payload).encode("utf-8")).hexdigest()
        profile = db.query(Profile).filter(Profile.fingerprint == profile_fingerprint).first()
        if not profile:
            profile = Profile(
                user_id=user.user_id,
                raw_tex_content=None,
                parsed_json_v=profile_payload,
                fingerprint=profile_fingerprint,
                created_at=datetime.utcnow(),
            )
            db.add(profile)
            db.commit()
            db.refresh(profile)

        # jobs -> companies + job_postings
        for doc in mongo.jobs.find({}):
            company_name = ((doc.get("company") or {}).get("name") or "Unknown Company").strip()[:150]
            company_id = company_by_name.get(company_name)
            if company_id is None:
                company = db.query(Company).filter(Company.name == company_name).first()
                if not company:
                    company = Company(
                        name=company_name,
                        website=((doc.get("company") or {}).get("website") or None),
                        industry=None,
                        created_at=datetime.utcnow(),
                    )
                    db.add(company)
                    db.flush()
                company_id = company.company_id
                company_by_name[company_name] = company_id

            fingerprint = (doc.get("fingerprint") or "").strip()
            if not fingerprint:
                fallback = f"{company_name}:{doc.get('title') or ''}:{doc.get('description') or ''}"
                fingerprint = hashlib.sha256(fallback.encode("utf-8")).hexdigest()

            posting = db.query(JobPosting).filter(JobPosting.fingerprint == fingerprint).first()
            if posting is None:
                posting = JobPosting(
                    company_id=company_id,
                    title=(doc.get("title") or "Untitled Job")[:200],
                    description=doc.get("description"),
                    source_platform=(doc.get("source") or None),
                    fingerprint=fingerprint,
                    salary_min=_safe_float((doc.get("compensation") or {}).get("min_amount")),
                    salary_max=_safe_float((doc.get("compensation") or {}).get("max_amount")),
                    status=_status_to_job(doc.get("status")),
                    created_at=datetime.utcnow(),
                )
                db.add(posting)
                db.flush()

            job_by_fingerprint[fingerprint] = posting.job_id

        db.commit()

        # job_scores
        for doc in mongo.job_scores.find({}):
            job_fp = (doc.get("job_fingerprint") or "").strip()
            job_id = job_by_fingerprint.get(job_fp)
            if not job_id:
                continue

            score = JobScore(
                job_id=job_id,
                profile_id=profile.profile_id,
                total_score=_safe_float(doc.get("final_score")),
                skill_score=_safe_float(doc.get("skill_relevance_score")),
                semantic_score=_safe_float(doc.get("semantic_context_score")),
                created_at=datetime.utcnow(),
            )
            db.add(score)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()

        # tailored_applications
        for doc in mongo.tailored_applications.find({}):
            job_fp = (doc.get("job_fingerprint") or "").strip()
            job_id = job_by_fingerprint.get(job_fp)
            if not job_id:
                continue

            tr = TailoredResume(
                job_id=job_id,
                profile_id=profile.profile_id,
                tailored_tex=doc.get("generated_tex_path"),
                status=_status_to_resume(doc.get("status")),
                created_at=doc.get("created_at") or datetime.utcnow(),
                updated_at=doc.get("last_updated") or datetime.utcnow(),
            )
            db.add(tr)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()

        db.commit()
        print("Migration completed.")
        print(f"Companies: {db.query(Company).count()}")
        print(f"Job postings: {db.query(JobPosting).count()}")
        print(f"Profiles: {db.query(Profile).count()}")
        print(f"Job scores: {db.query(JobScore).count()}")
        print(f"Tailored resumes: {db.query(TailoredResume).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    migrate()

