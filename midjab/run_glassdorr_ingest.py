#!/usr/bin/env python3
"""
run_glassdoor_ingest.py

Conductor script: performs a single end-to-end run of the V2 ingestion pipeline
for Glassdoor using the jobspy scraper and our Data Factory.
"""

import logging
import sys
from datetime import datetime
from typing import Any

# Project modules
from core.db import init_indexes, get_db
from agents.data_factory import process_raw_job

# Attempt to import jobspy scraper components
try:
    from jobspy.glassdoor import Glassdoor
    from jobspy.model import ScraperInput, Site, Country, DescriptionFormat
except Exception as e:
    Glassdoor = None
    ScraperInput = None
    Site = None
    Country = None
    DescriptionFormat = None
    print("Warning: jobspy imports failed. Ensure 'jobspy' is installed.")
    print(f"Import error: {e}")


# -------- configuration --------
SEARCH_TERM = "Machine Learning Engineer"
LOCATION = "Hyderabad"
# Glassdoor is sensitive to high volumes. Start small to test auth/blocking.
RESULTS_WANTED = 15 
DESCRIPTION_FORMAT = DescriptionFormat.HTML if DescriptionFormat is not None else "html"
TARGET_COUNTRY = Country.INDIA if Country is not None else "INDIA"
SITE = Site.GLASSDOOR if Site is not None else "glassdoor"
# -------------------------------


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def build_scraper_input() -> Any:
    """Build ScraperInput for Glassdoor."""
    if ScraperInput is None:
        return {}

    return ScraperInput(
        site_type=[SITE],
        country=TARGET_COUNTRY,
        search_term=SEARCH_TERM,
        location=LOCATION,
        results_wanted=RESULTS_WANTED,
        description_format=DescriptionFormat.HTML,
        # Glassdoor usually fetches full description by default in jobspy
    )


def run_scrape_and_ingest():
    setup_logging()
    logger = logging.getLogger("midjab.run_glassdoor_ingest")
    logger.info("Starting Glassdoor ingest run")

    logger.info("Initializing DB indexes...")
    init_indexes()

    if Glassdoor is None:
        logger.error("jobspy Glassdoor scraper not available.")
        return {"status": "failed", "reason": "jobspy_missing"}

    scraper_input = build_scraper_input()
    scraper = Glassdoor()

    logger.info("Running Glassdoor scraper (watching for 429/Block)...")
    try:
        job_response = scraper.scrape(scraper_input)
    except Exception as e:
        logger.exception("Glassdoor Scraper run failed")
        return {"status": "failed", "reason": "scrape_error", "error": str(e)}

    jobs = getattr(job_response, "jobs", None)
    if not jobs:
        logger.warning("Scraper returned no jobs. Possible IP block or empty search.")
        return {"status": "ok", "processed": 0, "inserted": 0, "updated": 0}

    processed = 0
    inserted = 0
    updated = 0

    logger.info(f"Processing {len(jobs)} jobs from Glassdoor")
    for job_post in jobs:
        processed += 1
        try:
            # Our factory handles the source labeling and adapting automatically
            result = process_raw_job(raw_job=job_post, source="glassdoor")
            
            status = result.get("status")
            fp = result.get("fingerprint")
            
            logger.info(f"[{processed}/{len(jobs)}] status={status} fingerprint={fp}")
            
            if status == "inserted":
                inserted += 1
            elif status == "updated":
                updated += 1
                
        except Exception as e:
            logger.exception(f"Processing failed for job #{processed}: {e}")

    logger.info("Ingest run complete")
    logger.info(f"Processed: {processed}, Inserted: {inserted}, Updated: {updated}")
    return {"status": "ok", "processed": processed, "inserted": inserted, "updated": updated}


if __name__ == "__main__":
    start = datetime.utcnow()
    summary = run_scrape_and_ingest()
    end = datetime.utcnow()
    elapsed = (end - start).total_seconds()
    print("--- Summary ---")
    print(summary)
    print(f"Elapsed seconds: {elapsed:.1f}")