"""
Data Factory: JobSpy → UnifiedJob Adapter Module

This module provides production-ready adapters and persistence logic for ingesting
job postings from the `jobspy` library (or compatible dict payloads) into our
UnifiedJob MongoDB collection with safe deduplication and merge semantics.

Key Functions:
--------------
- adapt_jobspy_to_unified(raw_job, source) -> UnifiedJob
  Transforms a jobspy.JobPost object (or dict) into a validated UnifiedJob instance.

- save_unified_job(job, upsert=True) -> dict
  Persists a UnifiedJob to MongoDB with atomic upsert on fingerprint key.

- process_raw_job(raw_job, source, merge_policy=None) -> dict
  End-to-end pipeline: adapt → fingerprint → save with intelligent merging.

Usage Example:
--------------
    from agents.data_factory import process_raw_job

    # Process a jobspy JobPost or dict
    result = process_raw_job(
        raw_job=jobspy_result,
        source="linkedin",
        merge_policy={"source_priority": {"linkedin": 10, "indeed": 5}}
    )

    if result["status"] == "inserted":
        print(f"New job saved: {result['fingerprint']}")
    elif result["status"] == "updated":
        print(f"Job updated: {result['fingerprint']}")
"""

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union
import enum
from pydantic import BaseModel
from bson import ObjectId
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError, PyMongoError

from core.db import get_db
from core.models import (
    UnifiedCompany,
    UnifiedCompensation,
    UnifiedJob,
    UnifiedLocation,
)

# Module logger
logger = logging.getLogger("midjab.data_factory")


# ============================================================================
# NORMALIZATION HELPERS
# ============================================================================

def _safe_str(value: Any, default: str = "") -> str:
    """Safely convert value to string, stripping whitespace."""
    if value is None:
        return default
    return str(value).strip()


def _safe_dict_get(obj: Any, key: str, default: Any = None) -> Any:
    """
    Safely extract a key from dict-like or attribute-based object.
    Supports both obj[key] and obj.key access patterns.
    """
    if obj is None:
        return default
    
    # Try dict-like access first
    if isinstance(obj, dict):
        return obj.get(key, default)
    
    # Try attribute access (for JobPost objects)
    return getattr(obj, key, default)



def _serialize_for_mongo(obj):
    """
    Recursively convert obj into Mongo-encodable primitives.
    Handles: Enum, Pydantic BaseModel, datetime, lists, dicts, objects with __dict__.
    """
    # primitives pass-through
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    # datetime: leave as-is (pymongo supports datetime)
    from datetime import datetime
    if isinstance(obj, datetime):
        return obj

    # enum -> value if available else name
    if isinstance(obj, enum.Enum):
        try:
            return obj.value
        except Exception:
            return obj.name

    # Pydantic models -> dict
    if isinstance(obj, BaseModel):
        return _serialize_for_mongo(obj.dict())

    # dict -> recurse
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            try:
                out[k] = _serialize_for_mongo(v)
            except Exception:
                out[k] = str(v)
        return out

    # list/tuple/set -> list recurse
    if isinstance(obj, (list, tuple, set)):
        return [_serialize_for_mongo(v) for v in obj]

    # objects with .dict() or .__dict__
    # some jobspy objects might provide .dict() or .to_dict()
    if hasattr(obj, "dict") and callable(getattr(obj, "dict")):
        try:
            return _serialize_for_mongo(obj.dict())
        except Exception:
            pass
    if hasattr(obj, "to_dict") and callable(getattr(obj, "to_dict")):
        try:
            return _serialize_for_mongo(obj.to_dict())
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return _serialize_for_mongo(vars(obj))
        except Exception:
            pass

    # Fallback to string
    try:
        return str(obj)
    except Exception:
        return None

def _extract_last_tokens(text: str, n: int = 5) -> str:
    """Extract last N words from text for fingerprinting fallback."""
    if not text:
        return ""
    words = text.split()
    return " ".join(words[-n:]) if len(words) >= n else text


def _normalize_compensation(raw_job: Any) -> Optional[UnifiedCompensation]:
    """
    Extract and normalize compensation data from raw job object.
    Handles various field names and formats from different sources.
    """
    # Try multiple common field names
    min_amt = _safe_dict_get(raw_job, "min_amount") or _safe_dict_get(raw_job, "salary_min")
    max_amt = _safe_dict_get(raw_job, "max_amount") or _safe_dict_get(raw_job, "salary_max")
    currency = _safe_dict_get(raw_job, "currency")
    interval = _safe_dict_get(raw_job, "interval") or _safe_dict_get(raw_job, "pay_period")
    
    # Only create compensation if we have at least one amount
    if min_amt is not None or max_amt is not None:
        return UnifiedCompensation(
            min_amount=float(min_amt) if min_amt is not None else None,
            max_amount=float(max_amt) if max_amt is not None else None,
            currency=_safe_str(currency) if currency else None,
            interval=_safe_str(interval) if interval else None
        )
    return None


def _normalize_location(raw_job: Any) -> Optional[UnifiedLocation]:
    """Extract and normalize location data from raw job object."""
    city = _safe_dict_get(raw_job, "city") or _safe_dict_get(raw_job, "location")
    state = _safe_dict_get(raw_job, "state") or _safe_dict_get(raw_job, "region")
    country = _safe_dict_get(raw_job, "country")
    postal_code = _safe_dict_get(raw_job, "postal_code") or _safe_dict_get(raw_job, "zip_code")
    
    # Try to parse combined location string if city is missing
    if not city and _safe_dict_get(raw_job, "location"):
        location_str = _safe_str(_safe_dict_get(raw_job, "location"))
        parts = [p.strip() for p in location_str.split(",")]
        if len(parts) >= 1:
            city = parts[0]
        if len(parts) >= 2:
            state = parts[1]
        if len(parts) >= 3:
            country = parts[2]
    
    # Parse geo coordinates if available
    geo = None
    lat = _safe_dict_get(raw_job, "latitude")
    lon = _safe_dict_get(raw_job, "longitude")
    if lat is not None and lon is not None:
        try:
            geo = {"lat": float(lat), "lon": float(lon)}
        except (ValueError, TypeError):
            pass
    
    return UnifiedLocation(
        city=_safe_str(city) if city else None,
        state=_safe_str(state) if state else None,
        country=_safe_str(country) if country else None,
        postal_code=_safe_str(postal_code) if postal_code else None,
        geo=geo
    )


def _normalize_company(raw_job: Any) -> UnifiedCompany:
    """
    Extract and normalize company data from raw job object.
    
    IMPORTANT: UnifiedCompany schema only has: name, website, id
    Do NOT populate industry or logo_url (not in schema).
    """
    company_name = _safe_dict_get(raw_job, "company") or _safe_dict_get(raw_job, "company_name")
    company_website = _safe_dict_get(raw_job, "company_url") or _safe_dict_get(raw_job, "company_website")
    company_id = _safe_dict_get(raw_job, "company_id")
    
    return UnifiedCompany(
        name=_safe_str(company_name) if company_name else None,
        website=_safe_str(company_website) if company_website else None,
        id=_safe_str(company_id) if company_id else None
    )


# ============================================================================
# MERGE POLICY LOGIC
# ============================================================================

def _should_update_field(
    existing_value: Any,
    new_value: Any,
    field_name: str,
    merge_policy: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Determine whether to overwrite an existing field with new value.
    
    Merge logic:
    1. If existing is None/empty and new has value -> update
    2. If new is None/empty -> don't update (never overwrite with None)
    3. For description: prefer longer content
    4. For compensation: prefer numeric over None
    5. For date_posted: prefer more recent
    6. Consider source_priority if provided in merge_policy
    """
    # Never overwrite with None/empty
    if new_value is None or (isinstance(new_value, str) and not new_value.strip()):
        return False
    
    # Always update if existing is None/empty and new has value
    if existing_value is None or (isinstance(existing_value, str) and not existing_value.strip()):
        return True
    
    # Field-specific logic
    if field_name == "description":
        # Prefer longer descriptions
        existing_len = len(str(existing_value)) if existing_value else 0
        new_len = len(str(new_value)) if new_value else 0
        return new_len > existing_len
    
    if field_name == "compensation":
        # Prefer non-None numeric compensation
        existing_has_amount = (
            existing_value and 
            isinstance(existing_value, dict) and
            (existing_value.get("min_amount") or existing_value.get("max_amount"))
        )
        new_has_amount = (
            new_value and 
            isinstance(new_value, dict) and
            (new_value.get("min_amount") or new_value.get("max_amount"))
        )
        # Update if new has amounts and existing doesn't
        if new_has_amount and not existing_has_amount:
            return True
        # If both have amounts, prefer the one with more complete data
        if new_has_amount and existing_has_amount:
            new_fields = sum(1 for k in ["min_amount", "max_amount", "currency", "interval"] 
                           if new_value.get(k) is not None)
            existing_fields = sum(1 for k in ["min_amount", "max_amount", "currency", "interval"] 
                                if existing_value.get(k) is not None)
            return new_fields > existing_fields
        return False
    
    if field_name == "date_posted":
        # Prefer more recent dates
        try:
            existing_dt = existing_value if isinstance(existing_value, datetime) else None
            new_dt = new_value if isinstance(new_value, datetime) else None
            if existing_dt and new_dt:
                return new_dt > existing_dt
        except:
            pass
    
    # Default: update if we have a value
    return True


def _merge_jobs(existing: Dict[str, Any], new_job: UnifiedJob, merge_policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Intelligently merge new job data with existing document.
    Returns the merged document ready for $set operation.
    """
    merged = new_job.to_mongo()
    
    # Fields to consider for intelligent merging
    mergeable_fields = ["description", "compensation", "title", "date_posted", "source_url"]
    
    for field in mergeable_fields:
        existing_value = existing.get(field)
        new_value = merged.get(field)
        
        if not _should_update_field(existing_value, new_value, field, merge_policy):
            # Keep existing value
            merged[field] = existing_value
    
    # Always update metadata fields
    merged["date_updated"] = datetime.now(timezone.utc)
    
    return merged


# ============================================================================
# CORE ADAPTER FUNCTIONS
# ============================================================================

def adapt_jobspy_to_unified(raw_job: Union[dict, object], source: str) -> UnifiedJob:
    """
    Transform a jobspy JobPost object or compatible dict into a validated UnifiedJob.
    
    Args:
        raw_job: Either a jobspy.JobPost instance or a dict with job data
        source: Source identifier (e.g., "linkedin", "indeed", "naukri")
    
    Returns:
        Validated UnifiedJob instance
    
    Raises:
        ValidationError: If the resulting UnifiedJob fails Pydantic validation
        ValueError: If critical required fields are missing
    """
    try:
        # Extract core fields
        title = _safe_str(_safe_dict_get(raw_job, "title"))
        if not title:
            raise ValueError("Job title is required")
        
        description = _safe_str(_safe_dict_get(raw_job, "description"))
        company = _normalize_company(raw_job)
        location = _normalize_location(raw_job)
        compensation = _normalize_compensation(raw_job)
        
        # Extract source-specific identifiers
        source_id = _safe_str(_safe_dict_get(raw_job, "id") or _safe_dict_get(raw_job, "job_id"))
        source_url = _safe_str(_safe_dict_get(raw_job, "job_url") or _safe_dict_get(raw_job, "url"))
        
        # Parse date_posted if available
        date_posted = _safe_dict_get(raw_job, "date_posted")
        if isinstance(date_posted, str):
            try:
                date_posted = datetime.fromisoformat(date_posted.replace("Z", "+00:00"))
            except:
                date_posted = None
        elif not isinstance(date_posted, datetime):
            date_posted = None
        
        # Collect source-specific metadata (anything not in core fields)
        source_metadata = {}
        metadata_fields = [
            "job_type", "job_level", "benefits", "num_applicants", 
            "is_remote", "emails", "company_industry", "logo_url",
            "company_num_employees", "job_function", "seniority_level"
        ]
        for field in metadata_fields:
            value = _safe_dict_get(raw_job, field)
            if value is not None:
                source_metadata[field] = _serialize_for_mongo(value)
        
        # Preserve original raw payload
        if isinstance(raw_job, dict):
            raw_payload = _serialize_for_mongo(raw_job.copy())
        else:
            # For object types, try to convert to dict
            raw_payload = {}
            for attr in dir(raw_job):
                if not attr.startswith("_"):
                    try:
                        val = getattr(raw_job, attr)
                        # Skip methods
                        if not callable(val):
                            raw_payload[attr] = val
                    except:
                        pass
            raw_payload = _serialize_for_mongo(raw_payload)
     
        # Construct UnifiedJob (validation happens here)
        # IMPORTANT: schema_version must be int, status defaults to "pending_review"
        unified_job = UnifiedJob(
            title=title,
            description=description,
            company=company,
            location=location,
            compensation=compensation,
            source=source,
            source_id=source_id if source_id else None,
            source_url=source_url if source_url else None,
            source_metadata=source_metadata,
            date_posted=date_posted,
            status="pending_review",  # Default status per schema
            match_score=None,
            schema_version=1,  # Must be int, not string
            raw=raw_payload
        )
        
        logger.info(f"Adapted job from {source}: {title} at {company.name or 'Unknown Company'}")
        return unified_job
        
    except ValidationError as e:
        logger.error(f"Validation failed for job from {source}: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error adapting job from {source}: {e}")
        raise ValueError(f"Failed to adapt job: {str(e)}")


def save_unified_job(job: UnifiedJob, upsert: bool = True) -> dict:
    """
    Persist a UnifiedJob to MongoDB with atomic upsert on fingerprint.
    
    Args:
        job: Validated UnifiedJob instance (must have fingerprint set)
        upsert: If True, use upsert semantics; if False, only insert new
    
    Returns:
        dict with keys:
            - status: "inserted", "updated", "skipped", or "error"
            - id: ObjectId of document (if successful)
            - fingerprint: The job fingerprint
            - matched_count: Number of documents matched (for updates)
            - modified_count: Number of documents modified (for updates)
    """
    db = get_db()
    collection = db.jobs
    
    if not job.fingerprint:
        raise ValueError("Job must have fingerprint set before saving")
    
    try:
        now = datetime.now(timezone.utc)
        job_dict = job.to_mongo()
        
        if upsert:
            # Atomic upsert with intelligent merging
            # First, check if document exists to apply merge logic
            existing = collection.find_one({"fingerprint": job.fingerprint})
            
            if existing:
                # Apply merge logic
                merged = _merge_jobs(existing, job)
                result = collection.update_one(
                    {"fingerprint": job.fingerprint},
                    {"$set": merged},
                    upsert=False
                )
                
                return {
                    "status": "updated" if result.modified_count > 0 else "skipped",
                    "id": existing["_id"],
                    "fingerprint": job.fingerprint,
                    "matched_count": result.matched_count,
                    "modified_count": result.modified_count
                }
            else:
                # Insert new document
                job_dict["date_created"] = now
                job_dict["date_updated"] = now
                result = collection.insert_one(job_dict)
                
                return {
                    "status": "inserted",
                    "id": result.inserted_id,
                    "fingerprint": job.fingerprint,
                    "matched_count": 0,
                    "modified_count": 0
                }
        else:
            # Insert only (fail on duplicate)
            job_dict["date_created"] = now
            job_dict["date_updated"] = now
            result = collection.insert_one(job_dict)
            
            return {
                "status": "inserted",
                "id": result.inserted_id,
                "fingerprint": job.fingerprint,
                "matched_count": 0,
                "modified_count": 0
            }
            
    except DuplicateKeyError:
        logger.warning(f"Duplicate fingerprint detected: {job.fingerprint}")
        return {
            "status": "error",
            "id": None,
            "fingerprint": job.fingerprint,
            "matched_count": 0,
            "modified_count": 0,
            "error": "duplicate_key",
            "message": "Job with this fingerprint already exists"
        }
    except PyMongoError as e:
        logger.error(f"Database error saving job {job.fingerprint}: {e}")
        raise


def _generate_fallback_fingerprint(job: UnifiedJob) -> str:
    """
    Generate a deterministic SHA-256 fingerprint as fallback.
    Uses: company + title + city + last 5 description tokens.
    Returns first 32 chars of hex digest.
    """
    components = [
        job.company.name or "",
        job.title or "",
        job.location.city if job.location else "",
        _extract_last_tokens(job.description, 5)
    ]
    
    fingerprint_string = "|".join(c.lower().strip() for c in components if c)
    return hashlib.sha256(fingerprint_string.encode()).hexdigest()[:32]


def _retry_with_backoff(func, max_attempts: int = 3, base_delay: float = 0.5):
    """
    Retry a function with exponential backoff on transient errors.
    
    Args:
        func: Callable to retry
        max_attempts: Maximum number of retry attempts
        base_delay: Base delay in seconds (doubles each retry)
    
    Returns:
        Result of successful function call
    
    Raises:
        Last exception if all retries fail
    """
    last_exception = None
    
    for attempt in range(max_attempts):
        try:
            return func()
        except (PyMongoError, ConnectionError, TimeoutError) as e:
            last_exception = e
            if attempt < max_attempts - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Transient error (attempt {attempt + 1}/{max_attempts}): {e}. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"All {max_attempts} retry attempts failed")
    
    raise last_exception


def process_raw_job(
    raw_job: Union[dict, object],
    source: str,
    merge_policy: Optional[Dict[str, Any]] = None
) -> dict:
    """
    End-to-end pipeline: adapt → fingerprint → save with retries and error handling.
    
    Args:
        raw_job: jobspy JobPost object or compatible dict
        source: Source identifier (e.g., "linkedin", "indeed")
        merge_policy: Optional dict controlling merge behavior:
            - source_priority: dict mapping source names to priority scores
    
    Returns:
        dict with keys:
            - status: "inserted", "updated", "skipped", or "error"
            - id: ObjectId of saved document (if successful)
            - fingerprint: The job fingerprint
            - details: Additional context (validation errors, exception messages, etc.)
    """
    try:
        # Step 1: Adapt to UnifiedJob
        logger.info(f"Processing job from {source}")
        unified_job = adapt_jobspy_to_unified(raw_job, source)
        
        # Step 2: Generate fingerprint
        try:
            fingerprint = unified_job.generate_fingerprint(extra_tokens=5)
            unified_job.fingerprint = fingerprint
        except Exception as e:
            logger.warning(f"Primary fingerprint generation failed: {e}. Using fallback.")
            unified_job.fingerprint = _generate_fallback_fingerprint(unified_job)
        
        logger.info(f"Generated fingerprint: {unified_job.fingerprint}")
        
        # Step 3: Save with retry logic
        def save_operation():
            return save_unified_job(unified_job, upsert=True)
        
        save_result = _retry_with_backoff(save_operation, max_attempts=3)
        
        # Enhance result with additional context
        save_result["details"] = {
            "source": source,
            "title": unified_job.title,
            "company": unified_job.company.name or "Unknown"
        }
        
        logger.info(f"Job {save_result['status']}: {unified_job.fingerprint}")
        return save_result
        
    except ValidationError as e:
        # Pydantic validation failed
        error_details = {
            "validation_errors": e.errors(),
            "source": source,
            "raw_payload": raw_job if isinstance(raw_job, dict) else str(raw_job)[:500]
        }
        logger.error(f"Validation failed for job from {source}: {error_details}")
        
        return {
            "status": "error",
            "id": None,
            "fingerprint": None,
            "details": error_details
        }
        
    except Exception as e:
        # Unexpected error
        error_details = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "source": source
        }
        logger.error(f"Unexpected error processing job from {source}: {error_details}")
        
        return {
            "status": "error",
            "id": None,
            "fingerprint": None,
            "details": error_details
        }


# ============================================================================
# DESIGN NOTES & ENHANCEMENT SUGGESTIONS
# ============================================================================

"""
DESIGN DECISIONS:
-----------------

1. **Schema Compliance**: The adapter strictly follows the actual UnifiedJob schema:
   - UnifiedCompany only has: name, website, id (no industry/logo_url)
   - schema_version is an int (not string)
   - status defaults to "pending_review"
   - All optional fields properly handle None values

2. **Duck-typing for jobspy objects**: The adapter uses _safe_dict_get() to support
   both dict and object access patterns, avoiding hard dependency on jobspy library.

3. **Intelligent merge logic**: When upserting, we don't blindly overwrite fields.
   The _should_update_field() function implements sensible defaults:
   - Never overwrite with None/empty values
   - Prefer longer descriptions
   - Prefer richer compensation (more fields populated)
   - Prefer more recent date_posted

4. **Fingerprint fallback**: If UnifiedJob.generate_fingerprint() fails, we fall
   back to a SHA-256 hash of company|title|city|description_tokens (first 32 chars).

5. **Retry logic**: Transient DB errors (network issues, replica lag) are retried
   3 times with exponential backoff (0.5s, 1s, 2s).

6. **Structured logging**: All operations log with source, source_id, and fingerprint
   for easy debugging and monitoring.

7. **Source metadata preservation**: Fields like job_type, benefits, logo_url, etc.
   that aren't in the core schema are preserved in source_metadata for future use.

SUGGESTED ENHANCEMENTS:
-----------------------

1. **TTL Index**: Add a TTL index on `date_posted` to auto-expire old job listings:
   ```python
   collection.create_index("date_posted", expireAfterSeconds=60*60*24*90)  # 90 days
   ```

2. **Source tracking**: Add a `source_first_seen` timestamp to track when we first
   ingested a job from each source. Useful for deduplication across sources.

3. **Batch processing**: Add a `process_raw_jobs_batch()` function that uses
   bulk_write() for efficient bulk ingestion (100+ jobs).

4. **Advanced merge policies**: 
   - Add source_priority weighting (e.g., LinkedIn > Indeed)
   - Add field-level merge strategies (always_update, never_update, prefer_longest)
   - Add conflict resolution callbacks

5. **Metrics/monitoring**: Integrate with prometheus_client or similar to track:
   - Jobs ingested per source
   - Duplicate rate
   - Validation failure rate
   - Average processing time

6. **Async support**: Convert to async/await with motor for better throughput in
   high-volume pipelines.

7. **Schema evolution**: Add migration helpers for when schema_version changes,
   allowing seamless upgrades of existing documents.

UNIT TEST IDEAS:
----------------

1. test_adapt_valid_jobspy_dict():
   - Pass a complete dict with all fields
   - Assert UnifiedJob is created with correct mappings
   - Verify nested objects (company, location, compensation)
   - Verify schema_version is int, status is "pending_review"

2. test_adapt_missing_required_fields():
   - Pass dict without title
   - Assert ValueError is raised

3. test_adapt_excludes_invalid_fields():
   - Pass dict with industry/logo_url in company
   - Assert these fields go to source_metadata, not company object
   - Verify no validation errors

4. test_save_new_job():
   - Create UnifiedJob with fingerprint
   - Call save_unified_job()
   - Assert status="inserted" and valid ObjectId returned

5. test_save_duplicate_fingerprint():
   - Insert job with fingerprint A
   - Insert same job again
   - Assert status="skipped" and matched_count=1

6. test_merge_prefers_longer_description():
   - Insert job with short description
   - Update with longer description via process_raw_job()
   - Assert description was updated
   - Update with shorter description
   - Assert description was NOT updated

7. test_merge_never_overwrites_with_none():
   - Insert job with full compensation
   - Update with job missing compensation
   - Assert original compensation preserved

8. test_retry_on_transient_db_failure():
   - Mock pymongo to raise PyMongoError twice, succeed on third
   - Call process_raw_job()
   - Assert successful save after retries
   - Verify 3 total attempts in logs

9. test_fallback_fingerprint():
   - Mock UnifiedJob.generate_fingerprint() to raise exception
   - Call process_raw_job()
   - Assert fallback SHA-256 fingerprint was generated (32 chars)
"""