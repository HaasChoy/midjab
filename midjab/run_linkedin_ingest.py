#!/usr/bin/env python3
"""
run_linkedin_ingest.py

Multi-query LinkedIn ingestion conductor for midjab V2.

Behavior:
 - Runs multiple AI/ML-related search terms
 - Polite rate-limiting between queries
 - De-duplicates within the run using generated fingerprint
 - Persists UnifiedJob docs via agents.data_factory.process_raw_job
"""

import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, List, Optional

# Project modules
from core.db import init_indexes, get_db
from agents.data_factory import process_raw_job, adapt_jobspy_to_unified

# Attempt to import jobspy scraper components
try:
    from jobspy.linkedin import LinkedIn
    from jobspy.model import ScraperInput, Site, Country, DescriptionFormat
except Exception as e:
    LinkedIn = None
    ScraperInput = None
    Site = None
    Country = None
    DescriptionFormat = None
    print("Warning: jobspy imports failed. Ensure 'jobspy' is installed to run live ingest.")
    print(f"Import error: {e}")


# -------------------- Configuration --------------------
SEARCH_TERMS: List[str] = [
    "Machine Learning Engineer", "ML Engineer", "Machine Learning Scientist",
    "Data Scientist", "AI Engineer", "Junior AI Engineer","Junior AI/ML Engineer","Junior AI Researcher" "Applied ML Engineer", "Research Scientist",
    "MLOps Engineer", "ML Infrastructure Engineer", "Deep Learning Engineer",
    "Computer Vision Engineer", "NLP Engineer", "Data Engineer","Junior Data Engineer", "AI Researcher",
    "Machine Learning Software Engineer", "Junior Machine Learning Engineer",
    "Principal Machine Learning Engineer", "Machine Learning Intern","Junior Machine Learning Intern",
    "Machine Learning Analyst", "Machine Learning Developer", "Machine Learning Consultant",
    "Machine Learning Architect", "Machine Learning Engineer", "Machine Learning Specialist",
    "Machine Learning Analyst", "Machine Learning Developer", "Machine Learning Consultant","Junior Machine Learning Analyst",
    "Junior Machine Learning Developer", "Junior Machine Learning Consultant",
    "Junior Machine Learning Architect", "Junior Machine Learning Engineer", "Junior Machine Learning Specialist",
    "Junior Machine Learning Analyst", "Junior Machine Learning Developer", "Junior Machine Learning Consultant",
    "Junior Machine Learning Architect", "Junior Machine Learning Engineer", "Junior Machine Learning Specialist",
]

LOCATION = "India"                      # location string used by jobspy
RESULTS_PER_TERM = 200                   # polite per-term batch
MAX_TOTAL_RESULTS = 2000000            # safety cap for entire run
DELAY_BETWEEN_QUERIES = 2.0             # seconds between queries
SCRAPER_RETRY_DELAY = 5.0               # seconds to wait on scraper failure
SCRAPER_MAX_RETRIES = 2                 # retry attempts for a failing scrape
TARGET_COUNTRY = Country.INDIA if Country is not None else "INDIA"
SITE = Site.LINKEDIN if Site is not None else "linkedin"
# -------------------------------------------------------


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def build_scraper_input(
    search_term: str,
    results_wanted: int,
    location: Optional[str] = None
) -> Any:
    """
    Build a ScraperInput for jobspy. Returns typed ScraperInput when available,
    otherwise returns a plain dict with the same fields (useful for errors).
    """
    loc = location if location is not None else LOCATION

    if ScraperInput is None or Site is None or Country is None or DescriptionFormat is None:
        return {
            "site_type": [SITE],
            "country": TARGET_COUNTRY,
            "search_term": search_term,
            "location": loc,
            "results_wanted": results_wanted,
            "description_format": DescriptionFormat.HTML if DescriptionFormat is not None else "html",
            "linkedin_fetch_description": True
        }

    return ScraperInput(
        site_type=[Site.LINKEDIN],
        country=TARGET_COUNTRY,
        search_term=search_term,
        location=loc,
        results_wanted=results_wanted,
        description_format=DescriptionFormat.HTML,
        linkedin_fetch_description=True
    )


def scrape_with_retries(scraper, scraper_input, max_retries: int = SCRAPER_MAX_RETRIES):
    """Call scraper.scrape(...) with a small retry/backoff loop."""
    attempt = 0
    while attempt <= max_retries:
        try:
            return scraper.scrape(scraper_input)
        except Exception as e:
            attempt += 1
            logging.getLogger("midjab.run_linkedin_ingest").warning(
                f"Scraper attempt {attempt}/{max_retries+1} failed: {e}"
            )
            if attempt > max_retries:
                logging.getLogger("midjab.run_linkedin_ingest").exception("Scraper failed after retries")
                raise
            time.sleep(SCRAPER_RETRY_DELAY * attempt)


def run_multi_query_ingest():
    setup_logging()
    logger = logging.getLogger("midjab.run_linkedin_ingest")
    logger.info("Starting multi-query LinkedIn ingest run")

    # Ensure DB indexes exist
    logger.info("Initializing DB indexes (idempotent)...")
    init_indexes()

    if LinkedIn is None:
        logger.error("jobspy LinkedIn scraper not available. Install jobspy and retry.")
        return {"status": "failed", "reason": "jobspy_missing"}

    scraper = LinkedIn()

    total_processed = 0
    total_inserted = 0
    total_updated = 0
    seen_fps = set()

    for term in SEARCH_TERMS:
        if total_processed >= MAX_TOTAL_RESULTS:
            logger.info("Reached MAX_TOTAL_RESULTS cap; stopping further queries.")
            break

        logger.info(f"Searching for '{term}' in {LOCATION} (limit {RESULTS_PER_TERM})")

        scraper_input = build_scraper_input(search_term=term, results_wanted=RESULTS_PER_TERM, location=LOCATION)

        try:
            job_response = scrape_with_retries(scraper, scraper_input)
        except Exception as e:
            logger.error(f"Giving up on term '{term}' after retries: {e}")
            # polite backoff, then continue to next term
            time.sleep(DELAY_BETWEEN_QUERIES * 2)
            continue

        jobs = getattr(job_response, "jobs", []) or []
        logger.info(f"Scraper returned {len(jobs)} jobs for '{term}'")

        for job_post in jobs:
            if total_processed >= MAX_TOTAL_RESULTS:
                break

            total_processed += 1

            # Fast path: adapt to UnifiedJob to obtain fingerprint and skip duplicates in-run
            try:
                unified = adapt_jobspy_to_unified(job_post, "linkedin")
            except Exception as e:
                logger.exception(f"Adaptation failed for a job in term '{term}': {e}")
                continue

            # Generate fingerprint (use model method; it may set job.fingerprint)
            try:
                fp = unified.generate_fingerprint(extra_tokens=5)
                unified.fingerprint = fp
            except Exception:
                # if generate_fingerprint fails, fallback to process_raw_job which already has fallback,
                # but here we produce a quick fingerprint to avoid duplicate processing
                fp = None

            if fp and fp in seen_fps:
                logger.debug(f"Duplicate in-run fingerprint {fp} - skipping")
                continue
            if fp:
                seen_fps.add(fp)

            # Now run the full process (which will also upsert into DB)
            try:
                result = process_raw_job(raw_job=job_post, source="linkedin")
            except Exception as e:
                logger.exception(f"Processing failed for job (term='{term}') : {e}")
                continue

            status = result.get("status")
            if status == "inserted":
                total_inserted += 1
            elif status == "updated":
                total_updated += 1

            logger.info(f"[{total_processed}] term='{term}' status={status} fp={result.get('fingerprint')}")

        # polite delay before next query
        logger.info(f"Sleeping {DELAY_BETWEEN_QUERIES}s before next query to be polite")
        time.sleep(DELAY_BETWEEN_QUERIES)

    logger.info("Multi-query ingest run complete")
    logger.info(f"Processed: {total_processed}, Inserted: {total_inserted}, Updated: {total_updated}")
    return {
        "status": "ok",
        "processed": total_processed,
        "inserted": total_inserted,
        "updated": total_updated
    }


if __name__ == "__main__":
    start = datetime.now(timezone.utc)
    summary = run_multi_query_ingest()
    end = datetime.now(timezone.utc)
    elapsed = (end - start).total_seconds()
    print("--- Summary ---")
    print(summary)
    print(f"Elapsed seconds: {elapsed:.1f}")
