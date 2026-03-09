"""
Development/testing endpoints — remove in production or protect with API key.

Requires the ``X-Dev-Key`` header to match the ``DEV_API_KEY`` env var
(defaults to "midjab-dev-key" in dev for convenience).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config.database import get_db_session
from core.orm_models import AuthSession, User

logger = logging.getLogger("midjab.api.dev")

router = APIRouter()

# ── Dev API key guard ──────────────────────────────────────────────────────────

DEV_API_KEY = os.getenv("DEV_API_KEY", "midjab-dev-key")


def require_dev_key(
    x_dev_key: str | None = Header(default=None, alias="X-Dev-Key"),
) -> None:
    """Reject requests without a valid dev API key."""
    if os.getenv("ENVIRONMENT") == "production":
        raise HTTPException(status_code=403, detail="Not available in production")
    if not x_dev_key or x_dev_key != DEV_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Dev-Key header")


# ── Schemas ────────────────────────────────────────────────────────────────────


class TestUserCreate(BaseModel):
    email: str
    name: str | None = None
    password: str = "testpass123"


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.post("/create-test-user", dependencies=[Depends(require_dev_key)])
def create_test_user(
    body: TestUserCreate,
    db: Session = Depends(get_db_session),
):
    """
    Create a test user and return a session token for testing.

    ⚠️ WARNING: Only enable in development/testing environments!
    Requires X-Dev-Key header.
    """
    # Check if user exists
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        user = existing
    else:
        # Create new user
        user = User(
            id=uuid.uuid4(),
            email=body.email,
            name=body.name or "Test User",
            email_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # Create a session token (simplified - Better Auth uses more complex tokens)
    session_token = str(uuid.uuid4())
    session = AuthSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token=session_token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(session)
    db.commit()

    return {
        "user_id": str(user.id),
        "email": user.email,
        "session_token": session_token,
        "cookie_value": f"{session_token}.dummy_signature",
        "message": "Test user created. Use session_token in Cookie header as 'better-auth.session_token'",
    }


@router.post("/create-test-user-with-resume", dependencies=[Depends(require_dev_key)])
def create_test_user_with_resume(
    body: TestUserCreate,
    db: Session = Depends(get_db_session),
):
    """
    Create a test user, session, and a sample resume for quick testing.
    Requires X-Dev-Key header.
    """
    from core.orm_models import Resume

    # Create user
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        user = existing
    else:
        user = User(
            id=uuid.uuid4(),
            email=body.email,
            name=body.name or "Test User",
            email_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # Create session
    session_token = str(uuid.uuid4())
    session = AuthSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token=session_token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(session)
    db.commit()

    # Create sample resume
    sample_resume = {
        "personal_info": {
            "name": body.name or "Test User",
            "email": body.email,
            "phone": "+1-555-0100",
            "location": "San Francisco, CA",
        },
        "summary": "Experienced software engineer with expertise in Python, FastAPI, and PostgreSQL.",
        "experience": [
            {
                "title": "Senior Software Engineer",
                "company": "Tech Corp",
                "location": "San Francisco, CA",
                "start_date": "2020-01",
                "end_date": "present",
                "bullets": [
                    "Developed REST APIs using FastAPI",
                    "Managed PostgreSQL databases",
                    "Led team of 5 engineers",
                ],
            }
        ],
        "education": [
            {
                "degree": "BS Computer Science",
                "school": "University of California",
                "graduation_year": "2018",
            }
        ],
        "skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "AWS"],
    }

    resume = Resume(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Test Resume",
        content_json=sample_resume,
        is_active=True,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    return {
        "user_id": str(user.id),
        "email": user.email,
        "session_token": session_token,
        "cookie_value": f"{session_token}.dummy_signature",
        "resume_id": str(resume.id),
        "message": "Test user and resume created. Use session_token in Cookie header.",
    }
