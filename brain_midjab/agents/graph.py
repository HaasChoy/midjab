from __future__ import annotations

import json
import os
from typing import Any

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from agents.nodes import (
    after_publisher,
    analyst_node,
    healer_node,
    publisher_node,
    scout_node,
    strategist_node,
    supervisor_node,
    supervisor_route,
)
from agents.state import HiveState
from config.database import SessionLocal, test_connection
from core.orm_models import Resume, User


def _write_profile_json(resume: Resume) -> None:
    os.makedirs("outputs", exist_ok=True)
    with open("outputs/user_profile.json", "w", encoding="utf-8") as f:
        json.dump(resume.content_json, f, indent=2)


def _resolve_user_and_resume(user_email: str, resume_id: str | None) -> tuple[str, str]:
    with SessionLocal() as session:
        user = session.execute(select(User).where(User.email == user_email)).scalar_one_or_none()
        if user is None:
            raise ValueError(f"User not found for email: {user_email}")

        if resume_id:
            resume = session.execute(
                select(Resume).where(Resume.id == resume_id, Resume.user_id == user.id)
            ).scalar_one_or_none()
        else:
            resume = session.execute(
                select(Resume).where(Resume.user_id == user.id, Resume.is_active == True).limit(1)  # noqa: E712
            ).scalar_one_or_none()

        if resume is None:
            raise ValueError("No resume found for user. Upload or activate a resume first.")

        _write_profile_json(resume)
        return str(user.id), str(resume.id)


def build_hive_graph():
    graph = StateGraph(HiveState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("scout", scout_node)
    graph.add_node("analyst", analyst_node)
    graph.add_node("strategist", strategist_node)
    graph.add_node("publisher", publisher_node)
    graph.add_node("healer", healer_node)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        supervisor_route,
        {
            "discovery": "scout",
            "scoring": "analyst",
            "tailoring": "strategist",
            "compiling": "publisher",
            "done": END,
        },
    )
    graph.add_edge("scout", "supervisor")
    graph.add_edge("analyst", "supervisor")
    graph.add_edge("strategist", "supervisor")
    graph.add_conditional_edges(
        "publisher",
        after_publisher,
        {
            "healer": "healer",
            "supervisor": "supervisor",
        },
    )
    graph.add_edge("healer", "publisher")
    return graph.compile()


def run_hive(
    *,
    user_email: str,
    resume_id: str | None = None,
    skip_discovery: bool = False,
    only_score: bool = False,
    only_tailor: bool = False,
    only_compile: bool = False,
    max_retries: int = 3,
    llm_model: str = "phi3.5",
    min_match_score: float = 6.0,
) -> dict[str, Any]:
    if not test_connection():
        raise RuntimeError("PostgreSQL is unreachable. Check DATABASE_URL / DB stack.")

    selected_modes = [only_score, only_tailor, only_compile]
    if sum(1 for m in selected_modes if m) > 1:
        raise ValueError("Use only one of: only_score, only_tailor, only_compile.")

    user_id, resolved_resume_id = _resolve_user_and_resume(user_email, resume_id)
    only_mode = "score" if only_score else "tailor" if only_tailor else "compile" if only_compile else None

    initial_state: HiveState = {
        "user_id": user_id,
        "resume_id": resolved_resume_id,
        "phase": "discovery" if not skip_discovery else "scoring",
        "discovery_done": skip_discovery,
        "only_mode": only_mode,
        "skip_discovery": skip_discovery,
        "min_match_score": float(min_match_score),
        "llm_model": llm_model,
        "new_job_ids": [],
        "pending_app_ids": [],
        "current_app_id": None,
        "compile_error": None,
        "retry_count": 0,
        "max_retries": int(max_retries),
        "messages": [f"Hive started for user={user_email} resume={resolved_resume_id}"],
    }

    app = build_hive_graph()
    final_state = app.invoke(initial_state)
    return final_state
