#!/usr/bin/env python3
"""MidJab V3 — LangGraph Hive Entrypoint."""

from __future__ import annotations

import argparse
import json
import sys

from agents.graph import run_hive


def main() -> None:
    parser = argparse.ArgumentParser(description="MidJab V3 Hive Pipeline")
    parser.add_argument("--user-email", required=True, help="User email")
    parser.add_argument("--resume-id", default=None, help="Optional resume UUID override")
    parser.add_argument("--skip-discovery", action="store_true")
    parser.add_argument("--only-score", action="store_true")
    parser.add_argument("--only-tailor", action="store_true")
    parser.add_argument("--only-compile", action="store_true")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--llm-model", default="phi3.5")
    parser.add_argument("--min-match-score", type=float, default=6.0)
    args = parser.parse_args()

    try:
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
        print(json.dumps(final_state, indent=2))
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
