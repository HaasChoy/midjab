from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select, update

from agents.resume_tailor import ResumeTailorV3, _make_base_content
from agents.state import HiveState
from config.database import SessionLocal
from core.orm_models import Application, Job


def _reflect_and_refine(
    tailor: ResumeTailorV3,
    app_id: str,
    draft: dict,
    job_description: str,
) -> dict:
    critique_prompt = f"""You are reviewing a tailored resume draft.
Find missing important job keywords and weak alignment areas.

JOB DESCRIPTION:
{job_description[:2000]}

DRAFT JSON:
{json.dumps(draft)[:4000]}

Respond ONLY with JSON:
{{
  "missing_keywords": ["kw1", "kw2"],
  "issues": ["issue1", "issue2"]
}}"""
    critique = tailor._call_llm(critique_prompt, "reflection_critique", app_id)
    missing = critique.get("missing_keywords", []) if isinstance(critique, dict) else []
    if not missing:
        return draft

    refine_prompt = f"""Refine this tailored resume draft to address critique.

CRITIQUE:
{json.dumps(critique)}

DRAFT JSON:
{json.dumps(draft)[:4000]}

Respond ONLY with JSON with any of keys: summary, skills, experience."""
    refined = tailor._call_llm(refine_prompt, "reflection_refine", app_id)
    if not isinstance(refined, dict):
        return draft

    updated = dict(draft)
    for key in ("summary", "skills", "experience"):
        if key in refined:
            updated[key] = refined[key]
    return updated


def strategist_node(state: HiveState) -> HiveState:
    next_state = dict(state)
    min_score = float(next_state.get("min_match_score", 6.0))
    tailored_ids: list[str] = []

    tailor = ResumeTailorV3(
        llm_model=next_state.get("llm_model", "gemini-1.5-flash"),
        min_match_score=min_score,
    )
    base_content = _make_base_content(tailor.user_profile)

    with SessionLocal() as session:
        rows = session.execute(
            select(Application, Job)
            .join(Job, Application.job_id == Job.id)
            .where(Application.status == "SCORED")
            .where(Application.match_score >= min_score)
            .order_by(Application.match_score.desc())
            .limit(20)
        ).all()

    for app, job in rows:
        app_id = str(app.id)
        try:
            with SessionLocal() as session:
                session.execute(
                    update(Application)
                    .where(Application.id == app.id)
                    .values(status="TAILORING", updated_at=datetime.now(timezone.utc))
                )
                session.commit()

            summary = tailor._tailor_summary(job.title, job.description or "", base_content.get("summary", ""), app.id)
            experience = tailor._tailor_bullets(job.title, job.description or "", base_content.get("experience", []), app.id)
            skills = tailor._tailor_skills(job.title, job.description or "", base_content.get("skills", []), app.id)

            draft = {
                **base_content,
                "summary": summary,
                "experience": experience,
                "skills": skills,
            }
            draft = _reflect_and_refine(tailor, app_id, draft, job.description or "")

            with SessionLocal() as session:
                session.execute(
                    update(Application)
                    .where(Application.id == app.id)
                    .values(
                        tailored_content=draft,
                        status="TAILORED",
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                session.commit()
            tailor._log_pipeline(app.id, "strategist", "tailored", "Tailoring complete with reflection.")
            tailored_ids.append(app_id)
        except Exception as exc:
            with SessionLocal() as session:
                session.execute(
                    update(Application)
                    .where(Application.id == app.id)
                    .values(status="FAILED", updated_at=datetime.now(timezone.utc))
                )
                session.commit()
            tailor._log_pipeline(app.id, "strategist", "tailor_error", str(exc))

    next_state["pending_app_ids"] = tailored_ids
    next_state["messages"] = [
        *next_state.get("messages", []),
        f"Strategist: tailored {len(tailored_ids)} applications.",
    ]
    return next_state
