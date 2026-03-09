"""
Pipeline routes — trigger the MidJab scoring / tailoring / compilation pipeline.
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.deps import get_current_user
from config.database import get_db_session
from core.orm_models import Resume, User
from core.schemas import PipelineRunRequest, PipelineRunResponse

logger = logging.getLogger("midjab.api.pipeline")

router = APIRouter()


@router.post("/run", response_model=PipelineRunResponse)
def run_pipeline(
    body: PipelineRunRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """Trigger the scoring → tailoring → compile pipeline for a specific resume."""

    # Verify ownership
    resume = (
        db.query(Resume)
        .filter(Resume.id == body.resume_id, Resume.user_id == current_user.id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    # Write the profile JSON that the existing pipeline agents expect
    os.makedirs("outputs", exist_ok=True)
    with open("outputs/user_profile.json", "w") as f:
        json.dump(resume.content_json, f, indent=4)

    logger.info("Pipeline triggered for resume %s by user %s", body.resume_id, current_user.id)

    # ── Run LangGraph hive pipeline ──
    try:
        from agents.graph import run_hive

        run_hive(
            user_email=current_user.email,
            resume_id=body.resume_id,
            skip_discovery=True,
        )
    except ImportError as exc:
        logger.error("Pipeline import error: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline stage import failed: {exc}",
        )
    except Exception as exc:
        logger.error("Pipeline error: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline execution failed: {exc}",
        )

    return PipelineRunResponse(
        message="Pipeline completed successfully",
        resume_id=body.resume_id,
    )
