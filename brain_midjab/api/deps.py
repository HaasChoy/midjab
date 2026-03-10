"""
Shared FastAPI dependencies.

`get_current_user` validates the Better Auth session by querying the
sessions table (cookie or Authorization: Bearer). Aligned with Better Auth:
sessions.token holds the session token; cookie may be raw token or token.signature.
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.database import get_db_session
from core.orm_models import AuthSession, User

logger = logging.getLogger("midjab.auth")

# Sliding window: extend session by this amount on each valid access
SESSION_EXTEND_MINUTES = int(os.getenv("SESSION_EXTEND_MINUTES", "30"))


def _normalize_token(raw: str) -> str:
    """Use token part before the dot (Better Auth may send token.signature)."""
    return raw.split(".")[0] if "." in raw else raw


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    better_auth_session_token: str | None = Cookie(
        default=None, alias="better-auth.session_token"
    ),
    db: Session = Depends(get_db_session),
) -> User:
    """Resolve the authenticated user from Better Auth session.

    Accepts either:
      - Cookie: better-auth.session_token (same-origin or proxied requests)
      - Header: Authorization: Bearer <token> (e.g. Postman, server-side)

    Better Auth stores the session token in sessions.token; the cookie may be
    the raw token or ``<token>.<signature>``. We use the token part before the dot.

    Security: timing-safe comparison, structured logging, sliding-window extension.
    """
    client_ip = request.client.host if request.client else "unknown"

    raw: str | None = None
    if authorization and authorization.startswith("Bearer "):
        raw = authorization[7:].strip()
    if not raw and better_auth_session_token:
        raw = better_auth_session_token

    if not raw:
        logger.warning("AUTH_FAIL | ip=%s | reason=missing_cookie_or_header", client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated — missing session cookie or Authorization: Bearer",
        )

    token = _normalize_token(raw)

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
