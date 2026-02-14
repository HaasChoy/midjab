#!/usr/bin/env python3
"""One-time migration utility from MidJab V2 MongoDB to MidJab V3 final schema."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from pymongo import MongoClient
from sqlalchemy.orm import Session

from config.database import SessionLocal
from core.orm_models import Application, Job, PipelineLog, Resume, User

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI_OLD", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB_OLD", "midjab_v2")
DEFAULT_MIGRATION_EMAIL = os.getenv("MIGRATION_USER_EMAIL", "migrated-user@midjab.local")


def _mongo() -> Any:
    return MongoClient(MONGO_URI)[MONGO_DB]


def _safe_decimal(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _job_status(status: str | None) -> str:
    value = (status or "NEW").upper()
    if value in {"NEW", "ACTIVE", "CLOSED", "FAILED", "SCORED"}:
        return value
    return "NEW"


def _application_status(status: str | None) -> str:
    value = (status or "PENDING").upper()
    if value in {"PENDING", "DRAFTING", "READY", "COMPILED", "FAILED"}:
        return value
    return "PENDING"


def _get_or_create_migration_user(db: Session) -> User:
    user = db.query(User).filter(User.email == DEFAULT_MIGRATION_EMAIL).first()
    if user:
        return user
    user = User(email=DEFAULT_MIGRATION_EMAIL, name="Migrated User", password_hash="MIGRATED_ACCOUNT")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def migrate() -> None:
    mongo = _mongo()
    db = SessionLocal()

    job_by_fingerprint: dict[str, uuid.UUID] = {}

    try:
        user = _get_or_create_migration_user(db)

        resume = db.query(Resume).filter(Resume.user_id == user.id, Resume.is_active.is_(True)).first()
        if resume is None:
            resume = Resume(
                user_id=user.id,
                name="Migrated Resume",
                content_json={"source": "migration"},
                raw_latex=None,
                is_active=True,
            )
            db.add(resume)
            db.commit()
            db.refresh(resume)

        # jobs
        for doc in mongo.jobs.find({}):
            fingerprint = (doc.get("fingerprint") or "").strip()
            if not fingerprint:
                fallback = f"{doc.get('company')}:{doc.get('title')}:{doc.get('description')}"
                fingerprint = uuid.uuid5(uuid.NAMESPACE_DNS, fallback).hex

            posting = db.query(Job).filter(Job.fingerprint == fingerprint).first()
            if posting is None:
                company_data = doc.get("company")
                company_name = (
                    (company_data.get("name") if isinstance(company_data, dict) else company_data) or "Unknown Company"
                )
                location_data = doc.get("location")
                if isinstance(location_data, dict):
                    location_text = ", ".join(
                        [p for p in [location_data.get("city"), location_data.get("state"), location_data.get("country")] if p]
                    ) or None
                else:
                    location_text = location_data

                compensation = doc.get("compensation") if isinstance(doc.get("compensation"), dict) else {}
                min_amt = _safe_decimal(compensation.get("min_amount"))
                max_amt = _safe_decimal(compensation.get("max_amount"))

                posting = Job(
                    fingerprint=fingerprint,
                    title=(doc.get("title") or "Untitled Job")[:255],
                    company=str(company_name)[:255],
                    location=(location_text[:255] if isinstance(location_text, str) else None),
                    description=doc.get("description"),
                    source=(doc.get("source") or None),
                    source_url=(doc.get("source_url") or None),
                    salary_min=int(min_amt) if min_amt is not None else None,
                    salary_max=int(max_amt) if max_amt is not None else None,
                    status=_job_status(doc.get("status")),
                )
                db.add(posting)
                db.flush()

            job_by_fingerprint[fingerprint] = posting.id

        db.commit()

        # applications from scoring output
        for doc in mongo.job_scores.find({}):
            job_fp = (doc.get("job_fingerprint") or "").strip()
            job_id = job_by_fingerprint.get(job_fp)
            if not job_id:
                continue

            app = db.query(Application).filter(Application.job_id == job_id, Application.resume_id == resume.id).first()
            if app is None:
                app = Application(job_id=job_id, resume_id=resume.id, status="PENDING")
                db.add(app)
                db.flush()

            app.match_score = _safe_decimal(doc.get("final_score"))
            app.score_reasoning = {
                "skill_score": _safe_decimal(doc.get("skill_relevance_score")),
                "semantic_score": _safe_decimal(doc.get("semantic_context_score")),
                "requirement_score": _safe_decimal(doc.get("requirement_fit_score")),
                "model_version": doc.get("model_version"),
            }

            db.add(
                PipelineLog(
                    application_id=app.id,
                    agent_name="opportunity_scorer",
                    action="score",
                    message="Migrated scoring data from v2.job_scores",
                    log_metadata={"source": "job_scores", "doc_id": str(doc.get("_id"))},
                )
            )

        # applications from tailored output
        for doc in mongo.tailored_applications.find({}):
            job_fp = (doc.get("job_fingerprint") or "").strip()
            job_id = job_by_fingerprint.get(job_fp)
            if not job_id:
                continue

            app = db.query(Application).filter(Application.job_id == job_id, Application.resume_id == resume.id).first()
            if app is None:
                app = Application(job_id=job_id, resume_id=resume.id, status="PENDING")
                db.add(app)
                db.flush()

            app.status = _application_status(doc.get("status"))
            app.tailored_content = doc.get("structured_content") if isinstance(doc.get("structured_content"), dict) else None
            app.generated_pdf_path = doc.get("final_pdf_path")

            db.add(
                PipelineLog(
                    application_id=app.id,
                    agent_name="resume_tailor",
                    action="tailor",
                    message="Migrated tailored data from v2.tailored_applications",
                    log_metadata={"source": "tailored_applications", "doc_id": str(doc.get("_id"))},
                )
            )

        db.add(
            PipelineLog(
                application_id=None,
                agent_name="migration",
                action="complete",
                message="MongoDB to PostgreSQL migration completed",
                log_metadata={"migrated_at": datetime.utcnow().isoformat()},
            )
        )

        db.commit()
        print("Migration completed.")
        print(f"Users: {db.query(User).count()}")
        print(f"Resumes: {db.query(Resume).count()}")
        print(f"Jobs: {db.query(Job).count()}")
        print(f"Applications: {db.query(Application).count()}")
        print(f"Pipeline logs: {db.query(PipelineLog).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    migrate()

