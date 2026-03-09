from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from agents.state import HiveState
from config.database import SessionLocal
from config.llm import call_llm, parse_llm_json
from core.orm_models import Application, PipelineLog


def _parse_json(content: str) -> dict[str, Any] | None:
    """Deprecated - now using parse_llm_json from config.llm"""
    return parse_llm_json(content)


def healer_node(state: HiveState) -> HiveState:
    next_state = dict(state)
    app_id = next_state.get("current_app_id")
    compile_error = next_state.get("compile_error") or "unknown compile error"
    if not app_id:
        next_state["messages"] = [*next_state.get("messages", []), "Healer: no current app id."]
        return next_state

    with SessionLocal() as session:
        app = session.execute(select(Application).where(Application.id == app_id)).scalar_one_or_none()
        if app is None:
            next_state["messages"] = [*next_state.get("messages", []), f"Healer: app {app_id} missing."]
            return next_state

        tailored = dict(app.tailored_content or {})
        fixed_content = dict(tailored)

        try:
            prompt = f"""You are fixing LaTeX resume content.
Compilation failed with this error:
{compile_error[:2500]}

Current tailored JSON:
{json.dumps(tailored)[:5000]}

Return ONLY valid JSON with key "tailored_content" containing fixed content."""
            response = call_llm(
                prompt=prompt,
                model=next_state.get("llm_model", "gemini-1.5-flash"),
                temperature=0.2,
                format_json=True,
                num_predict=1200,
            )
            if response and "message" in response:
                parsed = parse_llm_json(response["message"]["content"])
                if isinstance(parsed, dict):
                    if isinstance(parsed.get("tailored_content"), dict):
                        fixed_content = parsed["tailored_content"]
                    else:
                        for key in ("summary", "skills", "experience", "projects", "education", "contact_info", "full_name"):
                            if key in parsed:
                                fixed_content[key] = parsed[key]
        except Exception:
            fixed_content["latex_fix_notes"] = {
                "last_error": compile_error[:1000],
                "retry": int(next_state.get("retry_count", 0)),
            }

        session.execute(
            update(Application)
            .where(Application.id == app_id)
            .values(
                tailored_content=fixed_content,
                status="TAILORED",
                updated_at=datetime.now(timezone.utc),
            )
        )
        session.add(
            PipelineLog(
                application_id=app_id,
                agent_name="healer",
                action="latex_fix",
                message="Healer generated revised tailored content.",
                log_metadata={"retry_count": int(next_state.get("retry_count", 0))},
            )
        )
        session.commit()

    next_state["compile_error"] = None
    next_state["messages"] = [*next_state.get("messages", []), f"Healer: revised {app_id} and returned to publisher."]
    return next_state
