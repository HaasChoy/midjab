from __future__ import annotations

from sqlalchemy import select

from agents.state import HiveState
from config.database import SessionLocal
from core.orm_models import Application, Job


def _append(state: HiveState, message: str) -> None:
    state["messages"] = [*state.get("messages", []), message]


def supervisor_node(state: HiveState) -> HiveState:
    next_state = dict(state)

    if not next_state.get("discovery_done") and not next_state.get("skip_discovery"):
        next_state["phase"] = "discovery"
        _append(next_state, "Supervisor: routing to discovery.")
        return next_state

    with SessionLocal() as session:
        new_jobs = session.execute(select(Job.id).where(Job.status == "NEW")).all()
        scored_apps = session.execute(
            select(Application.id).where(
                Application.status == "SCORED",
                Application.match_score >= next_state.get("min_match_score", 6.0),
            )
        ).all()
        tailored_apps = session.execute(
            select(Application.id).where(Application.status == "TAILORED")
        ).all()

    only_mode = next_state.get("only_mode")
    if only_mode == "score":
        next_state["phase"] = "scoring" if new_jobs else "done"
        _append(next_state, f"Supervisor: only-score mode ({len(new_jobs)} NEW jobs).")
        return next_state
    if only_mode == "tailor":
        next_state["phase"] = "tailoring" if scored_apps else "done"
        _append(next_state, f"Supervisor: only-tailor mode ({len(scored_apps)} SCORED apps).")
        return next_state
    if only_mode == "compile":
        if tailored_apps:
            next_state["phase"] = "compiling"
            next_state["current_app_id"] = str(tailored_apps[0][0])
        else:
            next_state["phase"] = "done"
        _append(next_state, f"Supervisor: only-compile mode ({len(tailored_apps)} TAILORED apps).")
        return next_state

    if new_jobs:
        next_state["phase"] = "scoring"
        _append(next_state, f"Supervisor: found {len(new_jobs)} NEW jobs.")
    elif scored_apps:
        next_state["phase"] = "tailoring"
        _append(next_state, f"Supervisor: found {len(scored_apps)} SCORED apps to tailor.")
    elif tailored_apps:
        next_state["phase"] = "compiling"
        next_state["current_app_id"] = str(tailored_apps[0][0])
        _append(next_state, f"Supervisor: found {len(tailored_apps)} TAILORED apps to compile.")
    else:
        next_state["phase"] = "done"
        _append(next_state, "Supervisor: no remaining work.")

    return next_state


def supervisor_route(state: HiveState) -> str:
    phase = state.get("phase", "done")
    if phase in {"discovery", "scoring", "tailoring", "compiling", "done"}:
        return phase
    return "done"
