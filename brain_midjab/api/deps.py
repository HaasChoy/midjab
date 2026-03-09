"""
Shared FastAPI dependencies.

`get_current_user` validates the Better Auth session cookie by querying
the sessions table directly — no JS runtime needed on the Python side.
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.database import get_db_session
from core.orm_models import AuthSession, User

logger = logging.getLogger("midjab.auth")

# Sliding window: extend session by this amount on each valid access
SESSION_EXTEND_MINUTES = int(os.getenv("SESSION_EXTEND_MINUTES", "30"))


async def get_current_user(
    request: Request,
    better_auth_session_token: str | None = Cookie(
        default=None, alias="better-auth.session_token"
    ),
    db: Session = Depends(get_db_session),
) -> User:
    """Resolve the authenticated user from the Better Auth session cookie.

    Better Auth stores the cookie value as ``<token>.<signature>``.
    Only the *token* part is persisted in the ``sessions`` table.

    Security hardening:
      - Timing-safe token comparison
      - Structured logging for failed attempts
      - Sliding-window session extension
    """
    client_ip = request.client.host if request.client else "unknown"

    if not better_auth_session_token:
        logger.warning("AUTH_FAIL | ip=%s | reason=missing_cookie", client_ip)
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
        logger.warning("AUTH_FAIL | ip=%s | reason=invalid_token", client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )

    session_obj: AuthSession = row[0]
    user_obj: User = row[1]

    # Timing-safe comparison to defend against side-channel attacks
    if not hmac.compare_digest(session_obj.token, token):
        logger.warning("AUTH_FAIL | ip=%s | reason=token_mismatch", client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )

    # Check expiry
    now = datetime.now(timezone.utc)
    expires = session_obj.expires_at.replace(tzinfo=timezone.utc)

    if expires < now:
        logger.warning(
            "AUTH_FAIL | ip=%s | user=%s | reason=session_expired",
            client_ip,
            user_obj.id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired",
        )

    # Sliding-window: extend session expiry on each valid access
    if SESSION_EXTEND_MINUTES > 0:
        new_expiry = now + timedelta(minutes=SESSION_EXTEND_MINUTES)
        if new_expiry > expires:
            session_obj.expires_at = new_expiry
            db.commit()

    logger.debug("AUTH_OK | ip=%s | user=%s", client_ip, user_obj.id)
    return user_obj
