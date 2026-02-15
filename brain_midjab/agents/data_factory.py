"""
Data Factory V3 — PostgreSQL Edition
=====================================

Ingests raw job data (from jobspy scrapers or dicts) into the V3 `jobs` table.

Pipeline per job:
  1. Adapt raw data → flat dict matching the Job ORM columns
  2. Generate a SHA-256 fingerprint for dedup
  3. Upsert into `jobs` table (skip if fingerprint exists, or merge)
  4. Write a pipeline_log entry for auditability

All writes are transactional via SQLAlchemy sessions.
"""

import hashlib
import logging
import re
import time
import uuid
import enum
from datetime import datetime, timezone, date
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from config.database import SessionLocal
from core.orm_models import Job, PipelineLog

logger = logging.getLogger("midjab.data_factory")


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _safe_str(value: Any, default: str = "") -> str:
    """Safely convert value to string, stripping whitespace."""
    if value is None:
        return default
    return str(value).strip()


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    """Get a field from a dict or an object attribute."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _serialize(obj: Any) -> Any:
    """Recursively serialize complex objects to JSON-safe primitives."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, BaseModel):
        return _serialize(obj.model_dump())
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_serialize(v) for v in obj]
    if hasattr(obj, "model_dump"):
        return _serialize(obj.model_dump())
    if hasattr(obj, "__dict__"):
        return _serialize(vars(obj))
    return str(obj)


def _extract_last_tokens(text: str, n: int = 5) -> str:
    if not text:
        return ""
    words = text.split()
    return " ".join(words[-n:]) if len(words) >= n else text


# ─────────────────────────────────────────────
# FINGERPRINT
# ─────────────────────────────────────────────

def generate_fingerprint(
    company: str,
    title: str,
    location: Optional[str] = None,
    description: Optional[str] = None,
    extra_tokens: int = 5,
) -> str:
    """
    Deterministic SHA-256 fingerprint for job dedup.

    Strategy: lower(company) :: lower(title) :: lower(location) :: last_n_desc_tokens
    Returns the full 64-char hex digest.
    """
    parts = [
        (company or "").strip().lower(),
        (title or "").strip().lower(),
        (location or "").strip().lower(),
        _extract_last_tokens((description or "").lower(), extra_tokens),
    ]
    raw = "::".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────
# ADAPTER: raw job → flat dict for Job table
# ─────────────────────────────────────────────

def adapt_raw_to_job_dict(raw_job: Union[dict, object], source: str) -> Dict[str, Any]:
    """
    Convert a jobspy JobPost object (or a dict) to a flat dict
    matching the `jobs` table columns.

    Raises ValueError if the required 'title' field is missing.
    """
    title = _safe_str(_safe_get(raw_job, "title"))
    if not title:
        raise ValueError("Job title is required")

    company = _safe_str(
        _safe_get(raw_job, "company") or _safe_get(raw_job, "company_name")
    )
    if not company:
        company = "Unknown"

    # Location — try city, then location string
    location = _safe_str(_safe_get(raw_job, "location"))
    if not location:
        city = _safe_str(_safe_get(raw_job, "city"))
        state = _safe_str(_safe_get(raw_job, "state"))
        country = _safe_str(_safe_get(raw_job, "country"))
        parts = [p for p in (city, state, country) if p]
        location = ", ".join(parts) if parts else None

    description = _safe_str(_safe_get(raw_job, "description"))
    source_url = _safe_str(
        _safe_get(raw_job, "job_url") or _safe_get(raw_job, "url") or _safe_get(raw_job, "source_url")
    ) or None

    # Salary
    salary_min = _safe_get(raw_job, "min_amount") or _safe_get(raw_job, "salary_min")
    salary_max = _safe_get(raw_job, "max_amount") or _safe_get(raw_job, "salary_max")
    try:
        salary_min = int(salary_min) if salary_min is not None else None
    except (ValueError, TypeError):
        salary_min = None
    try:
        salary_max = int(salary_max) if salary_max is not None else None
    except (ValueError, TypeError):
        salary_max = None

    # Date posted
    posted_date = _safe_get(raw_job, "date_posted")
    if isinstance(posted_date, str):
        try:
            posted_date = datetime.fromisoformat(posted_date.replace("Z", "+00:00")).date()
        except (ValueError, TypeError):
            posted_date = None
    elif isinstance(posted_date, datetime):
        posted_date = posted_date.date()
    elif not isinstance(posted_date, date):
        posted_date = None

    # Fingerprint
    fingerprint = generate_fingerprint(company, title, location, description)

    return {
        "id": str(uuid.uuid4()),
        "fingerprint": fingerprint,
        "title": title[:255],
        "company": company[:255],
        "location": (location[:255] if location else None),
        "description": description or None,
        "source": (source[:50] if source else None),
        "source_url": source_url,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "posted_date": posted_date,
        "status": "NEW",
    }


# ─────────────────────────────────────────────
# MERGE LOGIC
# ─────────────────────────────────────────────

def _should_update(existing_val: Any, new_val: Any, field: str) -> bool:
    """Decide whether to overwrite an existing field value."""
    if new_val is None or (isinstance(new_val, str) and not new_val.strip()):
        return False
    if existing_val is None or (isinstance(existing_val, str) and not existing_val.strip()):
        return True
    if field == "description":
        return len(str(new_val)) > len(str(existing_val))
    return False  # default: keep existing non-null


# ─────────────────────────────────────────────
# CORE: save one job
# ─────────────────────────────────────────────

def save_job(job_dict: Dict[str, Any], upsert: bool = True) -> Dict[str, Any]:
    """
    Insert or upsert a single job into the `jobs` table.

    Returns a dict with status, fingerprint, and id.
    """
    fingerprint = job_dict["fingerprint"]

    with SessionLocal() as session:
        try:
            existing = session.execute(
                select(Job).where(Job.fingerprint == fingerprint)
            ).scalar_one_or_none()

            if existing:
                if not upsert:
                    return {"status": "skipped", "fingerprint": fingerprint, "id": str(existing.id)}

                # Intelligent merge — update only improved fields
                mergeable = ["description", "location", "source_url", "salary_min", "salary_max"]
                changed = False
                for field in mergeable:
                    new_val = job_dict.get(field)
                    old_val = getattr(existing, field, None)
                    if _should_update(old_val, new_val, field):
                        setattr(existing, field, new_val)
                        changed = True

                if changed:
                    session.commit()
                    return {"status": "updated", "fingerprint": fingerprint, "id": str(existing.id)}
                else:
                    return {"status": "skipped", "fingerprint": fingerprint, "id": str(existing.id)}
            else:
                job = Job(**job_dict)
                session.add(job)
                session.commit()
                return {"status": "inserted", "fingerprint": fingerprint, "id": str(job.id)}

        except IntegrityError:
            session.rollback()
            logger.warning("Duplicate fingerprint on insert: %s", fingerprint)
            return {"status": "duplicate", "fingerprint": fingerprint, "id": None}
        except SQLAlchemyError as e:
            session.rollback()
            logger.error("DB error saving job %s: %s", fingerprint, e)
            raise


def _log_pipeline(application_id: Optional[uuid.UUID], agent: str, action: str, message: str, metadata: Optional[dict] = None):
    """Write one pipeline_log row."""
    with SessionLocal() as session:
        log = PipelineLog(
            application_id=application_id,
            agent_name=agent[:50] if agent else None,
            action=action[:50] if action else None,
            message=message,
            log_metadata=metadata,
        )
        session.add(log)
        session.commit()


# ─────────────────────────────────────────────
# RETRY HELPER
# ─────────────────────────────────────────────

def _retry(func, max_attempts: int = 3, base_delay: float = 0.5):
    """Retry with exponential backoff on transient DB errors."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return func()
        except (SQLAlchemyError, ConnectionError, TimeoutError) as e:
            last_exc = e
            if attempt < max_attempts - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning("Transient error (attempt %d/%d): %s — retrying in %.1fs",
                               attempt + 1, max_attempts, e, delay)
                time.sleep(delay)
            else:
                logger.error("All %d retry attempts failed", max_attempts)
    raise last_exc


# ─────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────

def process_raw_job(
    raw_job: Union[dict, object],
    source: str,
) -> Dict[str, Any]:
    """
    End-to-end pipeline: adapt → fingerprint → save (with retries).

    Returns dict with status, fingerprint, id, and details.
    """
    try:
        logger.info("Processing job from %s", source)
        job_dict = adapt_raw_to_job_dict(raw_job, source)
        fingerprint = job_dict["fingerprint"]
        logger.info("Generated fingerprint: %s", fingerprint)

        result = _retry(lambda: save_job(job_dict, upsert=True))

        result["details"] = {
            "source": source,
            "title": job_dict["title"],
            "company": job_dict["company"],
        }

        # Log the ingest event
        _log_pipeline(
            application_id=None,
            agent="data_factory",
            action="ingest",
            message=f"{result['status']}: {job_dict['title']} @ {job_dict['company']}",
            metadata={"source": source, "fingerprint": fingerprint},
        )

        logger.info("Job %s: %s", result["status"], fingerprint)
        return result

    except ValueError as e:
        logger.error("Validation error from %s: %s", source, e)
        return {"status": "error", "fingerprint": None, "id": None, "details": {"error": str(e), "source": source}}
    except Exception as e:
        logger.error("Unexpected error from %s: %s", source, e)
        return {"status": "error", "fingerprint": None, "id": None, "details": {"error": str(e), "source": source}}


def process_raw_jobs_batch(
    raw_jobs: List[Union[dict, object]],
    source: str,
) -> Dict[str, int]:
    """
    Batch ingest. Returns summary counts.
    """
    counts = {"inserted": 0, "updated": 0, "skipped": 0, "duplicate": 0, "error": 0}
    for raw in raw_jobs:
        result = process_raw_job(raw, source)
        status = result.get("status", "error")
        counts[status] = counts.get(status, 0) + 1
    return counts
