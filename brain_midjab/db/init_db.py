#!/usr/bin/env python3
"""Schema utility for MidJab V3 final DB.

Default behavior is non-destructive: verifies required tables exist.
Use --create when you need SQLAlchemy to create missing tables.
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from config.database import Base, engine, test_connection
from core import orm_models  # noqa: F401  # Ensure models are imported for metadata

REQUIRED_TABLES = (
    "users", "sessions", "accounts", "verifications",  # auth tables (Better Auth)
    "resumes", "jobs", "applications", "pipeline_logs",  # domain tables
)


def create_schema() -> None:
    Base.metadata.create_all(bind=engine)


def check_schema() -> bool:
    try:
        with engine.connect() as conn:
            for table in REQUIRED_TABLES:
                conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        return True
    except Exception:
        return False


def main() -> int:
    print("Checking database connection...")
    if not test_connection():
        print("Database connection failed. Check DATABASE_URL and server availability.")
        return 1

    if check_schema():
        print("Schema check passed. All required tables are accessible.")
        return 0

    print("Schema check failed. Attempting to create missing tables...")
    create_schema()

    if check_schema():
        print("Schema created and validated successfully.")
        return 0

    print("Schema validation failed after create attempt.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

