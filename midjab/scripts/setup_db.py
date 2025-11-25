# scripts/setup_db.py
"""
Run this script once during deployment or during initial setup to ensure the DB has
the required indexes. Safe to re-run; index creation calls are idempotent when the
index already exists.
"""
import os
import sys

# Make sure project root is on PYTHONPATH if needed
# sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import init_indexes

def main():
    # Run with background indexing to avoid locking production DB for long periods.
    # If you want synchronous creation, call init_indexes(background=False)
    print("Initializing DB indexes (background=True)...")
    init_indexes(background=True)
    print("Done. If you had a large collection, indexes may still be building in background.")

if __name__ == "__main__":
    main()
