#!/usr/bin/env python3
"""
Cleanup expired sessions from the database.

Run manually or via cron:
    cd brain_midjab && python -m scripts.cleanup_sessions

Safe to run any time — only deletes rows where expires_at < NOW().
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from config.database import engine, test_connection
from core.orm_models import AuthSession


def cleanup_expired_sessions() -> int:
    """Delete all expired sessions. Returns count of deleted rows."""
    if not test_connection():
        print("ERROR: Cannot connect to database.")
        sys.exit(1)

    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        # Count before deletion
        count_stmt = select(func.count()).select_from(AuthSession.__table__).where(
            AuthSession.expires_at < now
        )
        expired_count = conn.execute(count_stmt).scalar() or 0

        if expired_count == 0:
            print("✅ No expired sessions found.")
            return 0

        # Delete expired sessions
        del_stmt = delete(AuthSession.__table__).where(
            AuthSession.expires_at < now
        )
        conn.execute(del_stmt)

    print(f"✅ Deleted {expired_count} expired session(s).")
    return expired_count


if __name__ == "__main__":
    cleanup_expired_sessions()
