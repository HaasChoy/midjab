#!/usr/bin/env python3
"""
Initialize MidJab V3 PostgreSQL schema.

Usage:
  python db/init_db.py
  python db/init_db.py --drop-first
"""

from __future__ import annotations

import argparse
import sys

from config.database import Base, engine, test_connection
from core import orm_models  # noqa: F401  # Ensure models are imported for metadata


def create_schema() -> None:
    Base.metadata.create_all(bind=engine)


def drop_schema() -> None:
    Base.metadata.drop_all(bind=engine)


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize MidJab V3 DB schema")
    parser.add_argument("--drop-first", action="store_true", help="Drop all existing tables before create")
    args = parser.parse_args()

    print("Checking database connection...")
    if not test_connection():
        print("Database connection failed. Check DATABASE_URL and server availability.")
        return 1

    if args.drop_first:
        print("Dropping existing schema...")
        drop_schema()

    print("Creating schema...")
    create_schema()
    print("Schema initialized successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

