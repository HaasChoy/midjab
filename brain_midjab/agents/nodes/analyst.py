from __future__ import annotations

from sqlalchemy import select

from agents.opportunity_scorer import OpportunityScorerV3
from agents.state import HiveState
from config.database import SessionLocal
from core.orm_models import Job


def analyst_node(state: HiveState) -> HiveState:
    next_state = dict(state)
    pending_app_ids: list[str] = []

    scorer = OpportunityScorerV3(
        llm_model=next_state.get("llm_model", "gemini-1.5-flash"),
        resume_id=next_state.get("resume_id"),
    )
    scorer.load_user_profile()

    with SessionLocal() as session:
        jobs = session.execute(
            select(Job).where(Job.status == "NEW").limit(50)
        ).scalars().all()
        job_data = [
            {"id": j.id, "title": j.title, "description": j.description or "", "fingerprint": j.fingerprint}
            for j in jobs
        ]

    for jd in job_data:
        result = scorer.score_job(jd["id"], jd["title"], jd["description"], jd["fingerprint"])
        if result and result.get("application_id"):
            pending_app_ids.append(str(result["application_id"]))

    next_state["pending_app_ids"] = pending_app_ids
    next_state["messages"] = [
        *next_state.get("messages", []),
        f"Analyst: scored {len(pending_app_ids)} applications.",
    ]
    return next_state
