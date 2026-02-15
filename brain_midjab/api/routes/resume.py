"""
Resume routes — PDF upload, list, and detail.

Flow:
  1. User uploads a PDF resume
  2. Text is extracted via pdfplumber
  3. Text is sent to the LLM profile parser → structured JSON
  4. JSON is stored in the `resumes` table
  5. Temp PDF is deleted
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from api.deps import get_current_user
from config.database import get_db_session
from core.orm_models import Resume, User
from core.schemas import ResumeRead, ResumeUploadResponse

logger = logging.getLogger("midjab.api.resume")

router = APIRouter()


# ─── Helpers ───────────────────────────────────────────────────────────────────


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract all text from a PDF using pdfplumber."""
    import pdfplumber

    text_parts: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts).strip()


# ─── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/upload-pdf", response_model=ResumeUploadResponse)
async def upload_resume_pdf(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """Upload a PDF resume → parse → store structured JSON → delete PDF."""

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    tmp_path: str | None = None
    try:
        # 1. Save uploaded bytes to a temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            contents = await file.read()
            if len(contents) == 0:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            tmp.write(contents)
            tmp_path = tmp.name

        # 2. Extract text from the PDF
        raw_text = extract_text_from_pdf(tmp_path)
        if not raw_text:
            raise HTTPException(
                status_code=422,
                detail="Could not extract text from the PDF — is it scanned / image-only?",
            )

        logger.info("Extracted %d chars from PDF '%s'", len(raw_text), file.filename)

        # 3. Structure via the LLM profile parser (reuse existing agent)
        from agents.profile_parser import extract_structured_data

        structured_json = extract_structured_data(raw_text)
        if not structured_json:
            raise HTTPException(
                status_code=500,
                detail="LLM failed to structure the resume — try again",
            )

        # 4. Persist in the resumes table
        resume = Resume(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            name=file.filename,
            content_json=structured_json,
            is_active=True,
        )
        db.add(resume)
        db.commit()
        db.refresh(resume)

        logger.info("Resume %s stored for user %s", resume.id, current_user.id)

        return ResumeUploadResponse(
            resume_id=resume.id,
            name=resume.name,
            content_json=structured_json,
            message="Resume parsed and stored successfully",
        )

    finally:
        # 5. Always clean up the temp PDF
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
            logger.debug("Deleted temp PDF %s", tmp_path)


@router.get("/list", response_model=list[ResumeRead])
def list_resumes(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """List all resumes belonging to the authenticated user."""
    resumes = (
        db.query(Resume)
        .filter(Resume.user_id == current_user.id)
        .order_by(Resume.created_at.desc())
        .all()
    )
    return [
        ResumeRead(
            id=r.id,
            user_id=r.user_id,
            name=r.name,
            content_json=r.content_json,
            is_active=r.is_active,
            created_at=r.created_at,
        )
        for r in resumes
    ]


@router.get("/{resume_id}", response_model=ResumeRead)
def get_resume(
    resume_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """Get a single resume by ID (must belong to the current user)."""
    resume = (
        db.query(Resume)
        .filter(Resume.id == resume_id, Resume.user_id == current_user.id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    return ResumeRead(
        id=resume.id,
        user_id=resume.user_id,
        name=resume.name,
        content_json=resume.content_json,
        is_active=resume.is_active,
        created_at=resume.created_at,
    )
