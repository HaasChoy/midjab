#!/usr/bin/env python3
"""MidJab V3 — LangGraph Hive Entrypoint."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from agents.graph import run_hive

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(module)s:%(funcName)s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr
)
logger = logging.getLogger("MidJabCLI")


def main() -> None:
    start_time = time.time()
    parser = argparse.ArgumentParser(description="MidJab V3 Hive Pipeline")
    parser.add_argument("--user-email", required=True, help="User email")
    parser.add_argument("--resume-id", default=None, help="Optional resume UUID override")
    parser.add_argument("--skip-discovery", action="store_true")
    parser.add_argument("--only-score", action="store_true")
    parser.add_argument("--only-tailor", action="store_true")
    parser.add_argument("--only-compile", action="store_true")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--llm-model", default="gemini-1.5-flash")
    parser.add_argument("--min-match-score", type=float, default=6.0)
    args = parser.parse_args()

    try:
        logger.info("Starting pipeline for user_email=%s", args.user_email)
        final_state = run_hive(
            user_email=args.user_email,
            resume_id=args.resume_id,
            skip_discovery=args.skip_discovery,
            only_score=args.only_score,
            only_tailor=args.only_tailor,
            only_compile=args.only_compile,
            max_retries=args.max_retries,
            llm_model=args.llm_model,
            min_match_score=args.min_match_score,
        )
        elapsed = time.time() - start_time
        logger.info("Pipeline completed successfully in %.2fs", elapsed)
        print(json.dumps(final_state, indent=2))
    except KeyboardInterrupt:
        logger.warning("Pipeline interrupted by user.")
        sys.exit(130)
    except Exception as exc:
        logger.critical("Pipeline failed unexpectedly: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
