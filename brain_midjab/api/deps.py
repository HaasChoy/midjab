"""
Shared FastAPI dependencies.

`get_current_user` validates the Better Auth session cookie by querying
the sessions table directly — no JS runtime needed on the Python side.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.database import get_db_session
from core.orm_models import AuthSession, User


async def get_current_user(
    better_auth_session_token: str | None = Cookie(
        default=None, alias="better-auth.session_token"
    ),
    db: Session = Depends(get_db_session),
) -> User:
    """Resolve the authenticated user from the Better Auth session cookie.

    Better Auth stores the cookie value as ``<token>.<signature>``.
    Only the *token* part is persisted in the ``sessions`` table.
    """
    if not better_auth_session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated — missing session cookie",
        )

    # Extract the token portion (before the dot)
    token = (
        better_auth_session_token.split(".")[0]
        if "." in better_auth_session_token
        else better_auth_session_token
    )

    # Look up session + user in one query
    stmt = (
        select(AuthSession, User)
        .join(User, AuthSession.user_id == User.id)
        .where(AuthSession.token == token)
    )
    row = db.execute(stmt).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )

    session_obj: AuthSession = row[0]
    user_obj: User = row[1]

    # Check expiry
    if session_obj.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired",
        )

    return user_obj
