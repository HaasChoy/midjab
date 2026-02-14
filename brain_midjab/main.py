#!/usr/bin/env python3
"""
midjab V2 - Grand Orchestrator (Robust Edition)
===============================================
"""
import argparse
import logging
import sys
import json
import os
from datetime import datetime

# --- Core Imports ---
# We keep DB imports global as they are needed for init
try:
    from core.db import init_indexes
    from agents.profile_parser import parse_resume_from_latex, extract_structured_data
except ImportError as e:
    print(f"CRITICAL CORE ERROR: {e}")
    sys.exit(1)

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("midjab_orchestrator")

def run_profile_step():
    """Stage 1: Parse LaTeX resume to JSON."""
    logger.info("--- Stage 1: Profile Parsing ---")
    if os.path.exists("outputs/user_profile.json"):
        logger.info("User profile already exists. Skipping parse.")
        return True

    raw_text = parse_resume_from_latex()
    if not raw_text:
        logger.error("Failed to read base resume.")
        return False

    structured_data = extract_structured_data(raw_text)
    if structured_data:
        os.makedirs("outputs", exist_ok=True)
        with open("outputs/user_profile.json", 'w') as f:
            json.dump(structured_data, f, indent=4)
        logger.info("Profile saved to outputs/user_profile.json")
        return True
    return False

def run_ingest_step():
    """Stage 2: Run Data Factory (Ingestion)."""
    logger.info("--- Stage 2: Data Ingestion (LinkedIn) ---")
    try:
        # LAZY IMPORT: Only imports if this step is actually run
        from run_linkedin_ingest import run_scrape_and_ingest
        summary = run_scrape_and_ingest()
        logger.info(f"Ingestion Result: {summary}")
    except ImportError:
        logger.error("Could not import 'run_linkedin_ingest'. Check if the file exists and has 'run_scrape_and_ingest' defined.")
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")

def run_scoring_step():
    """Stage 3: Opportunity Scorer."""
    logger.info("--- Stage 3: Scoring (Hybrid LLM) ---")
    try:
        # LAZY IMPORT
        from agents.opportunity_scorer import OpportunityScorerV2
        scorer = OpportunityScorerV2(llm_model="phi3.5")
        scorer.load_user_profile()
        scorer.run_full_scoring(batch_size=50)
    except ImportError:
        logger.error("Could not import 'agents.opportunity_scorer_v2'. Check your agents folder.")
    except Exception as e:
        logger.error(f"Scoring failed: {e}")

def run_tailoring_step():
    """Stage 4: Resume Tailor (The Writer)."""
    logger.info("--- Stage 4: Resume Tailoring (The Writer) ---")
    try:
        # LAZY IMPORT
        from agents.resume_tailor import ResumeTailorV2
        tailor = ResumeTailorV2(llm_model="phi3.5", min_match_score=6.0)
        tailor.run_tailoring_pipeline(batch_size=20)
    except ImportError:
        logger.error("Could not import 'agents.resume_tailor_v2'. Check your agents folder.")
    except Exception as e:
        logger.error(f"Tailoring failed: {e}")

def run_compiler_step():
    """Stage 5: LaTeX Architect (The Typesetter)."""
    logger.info("--- Stage 5: Compilation (The Typesetter) ---")
    try:
        # LAZY IMPORT
        from agents.latex_architect import LatexArchitect
        architect = LatexArchitect()
        architect.run_compiler_pipeline(batch_size=20)
    except ImportError:
        logger.error("Could not import 'agents.latex_architect'. Check your agents folder.")
    except Exception as e:
        logger.error(f"Compilation failed: {e}")

def main():
    parser = argparse.ArgumentParser(description="midjab V2 Pipeline Orchestrator")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip scraping")
    parser.add_argument("--only-score", action="store_true", help="Run only scoring")
    parser.add_argument("--only-tailor", action="store_true", help="Run only tailoring")
    parser.add_argument("--only-compile", action="store_true", help="Run only compilation")
    args = parser.parse_args()

    print("\n🚀 STARTING MIDJAB V2 PIPELINE\n")

    init_indexes()

    if not run_profile_step():
        sys.exit(1)

    if args.only_score:
        run_scoring_step()
        return
    
    if args.only_tailor:
        run_tailoring_step()
        return

    if args.only_compile:
        run_compiler_step()
        return

    # If skipping ingest, we NEVER touch the broken run_linkedin_ingest.py file
    if not args.skip_ingest:
        run_ingest_step()
    
    run_scoring_step()
    run_tailoring_step()
    run_compiler_step()

    print("\n✅ MIDJAB V2 RUN COMPLETE")

if __name__ == "__main__":
    main()