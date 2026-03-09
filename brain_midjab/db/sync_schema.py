#!/usr/bin/env python3
"""
Sync DB schema — adds missing columns/tables to match ORM models.

Safe to run multiple times (uses IF NOT EXISTS).
Does NOT drop anything.
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from config.database import engine, test_connection


# ── ALTER existing tables: add missing columns ──
ALTER_STATEMENTS = [
    # users table — add Better Auth columns
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS image TEXT;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();",
]

# ── CREATE missing auth tables (using UUID to match existing DB schema) ──
CREATE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        token TEXT UNIQUE NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
        ip_address VARCHAR(255),
        user_agent TEXT,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);",

    """
    CREATE TABLE IF NOT EXISTS accounts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        access_token TEXT,
        refresh_token TEXT,
        id_token TEXT,
        access_token_expires_at TIMESTAMP WITH TIME ZONE,
        refresh_token_expires_at TIMESTAMP WITH TIME ZONE,
        scope TEXT,
        password TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_accounts_user_id ON accounts(user_id);",

    """
    CREATE TABLE IF NOT EXISTS verifications (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        identifier TEXT NOT NULL,
        value TEXT NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """,
]


def run_migrations() -> None:
    print("Connecting to database...")
    if not test_connection():
        print("ERROR: Cannot connect to database. Check DATABASE_URL.")
        sys.exit(1)

    print("✅ Database connected.\n")

    with engine.begin() as conn:
        # Step 1: ALTER existing tables
        print("Adding missing columns to existing tables...")
        for sql in ALTER_STATEMENTS:
            try:
                conn.execute(text(sql))
                # Extract table.column from the statement for logging
                parts = sql.split("ADD COLUMN IF NOT EXISTS ")
                if len(parts) > 1:
                    col = parts[1].split()[0]
                    table = sql.split("ALTER TABLE ")[1].split()[0]
                    print(f"  ✅ {table}.{col}")
                else:
                    print(f"  ✅ {sql[:60]}")
            except Exception as e:
                print(f"  ⚠️  {sql[:60]}... → {e}")

        print()

        # Step 2: CREATE missing tables
        print("Creating missing auth tables...")
        for sql in CREATE_STATEMENTS:
            try:
                conn.execute(text(sql))
                if "CREATE TABLE" in sql:
                    table = sql.split("CREATE TABLE IF NOT EXISTS ")[1].split()[0]
                    print(f"  ✅ Table: {table}")
                elif "CREATE INDEX" in sql:
                    idx = sql.split("CREATE INDEX IF NOT EXISTS ")[1].split()[0]
                    print(f"  ✅ Index: {idx}")
            except Exception as e:
                print(f"  ⚠️  {sql.strip()[:60]}... → {e}")

    print("\n✅ Schema sync complete!")

    # Verify all tables
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(engine)
    tables = insp.get_table_names()
    required = ["users", "sessions", "accounts", "verifications", "resumes", "jobs", "applications", "pipeline_logs"]
    missing = [t for t in required if t not in tables]
    if missing:
        print(f"⚠️  Missing tables: {missing}")
    else:
        print("✅ All required tables verified.")


if __name__ == "__main__":
    run_migrations()
