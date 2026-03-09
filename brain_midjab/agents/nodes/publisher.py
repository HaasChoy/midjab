from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update

from agents.latex_architect import LatexArchitect
from agents.state import HiveState
from config.database import SessionLocal
from core.orm_models import Application, PipelineLog


def publisher_node(state: HiveState) -> HiveState:
    next_state = dict(state)
    app_id = next_state.get("current_app_id")

    if not app_id:
        with SessionLocal() as session:
            app = session.execute(
                select(Application).where(Application.status == "TAILORED").limit(1)
            ).scalar_one_or_none()
            if app is None:
                next_state["messages"] = [*next_state.get("messages", []), "Publisher: no TAILORED apps found."]
                return next_state
            app_id = str(app.id)
            next_state["current_app_id"] = app_id

    architect = LatexArchitect()
    with SessionLocal() as session:
        app = session.execute(select(Application).where(Application.id == app_id)).scalar_one()
        session.execute(
            update(Application)
            .where(Application.id == app_id)
            .values(status="COMPILING", updated_at=datetime.now(timezone.utc))
        )
        session.commit()

        try:
            template_data = architect._prepare_template_data(app.tailored_content or {})
            tex_content = architect._render_template(template_data)
            with tempfile.TemporaryDirectory() as tmp:
                success, pdf_path, error_log = architect._compile_pdf(tex_content, Path(tmp), app_id)

            if success:
                session.execute(
                    update(Application)
                    .where(Application.id == app_id)
                    .values(
                        status="COMPILED",
                        generated_pdf_path=pdf_path,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                session.add(
                    PipelineLog(
                        application_id=app_id,
                        agent_name="publisher",
                        action="compile_success",
                        message=pdf_path,
                    )
                )
                session.commit()
                next_state["compile_error"] = None
                next_state["retry_count"] = 0
                next_state["current_app_id"] = None
                next_state["messages"] = [*next_state.get("messages", []), f"Publisher: compiled {app_id}."]
                return next_state

            session.execute(
                update(Application)
                .where(Application.id == app_id)
                .values(status="FAILED", updated_at=datetime.now(timezone.utc))
            )
            session.add(
                PipelineLog(
                    application_id=app_id,
                    agent_name="publisher",
                    action="compile_failed",
                    message=(error_log or "compile failure")[:5000],
                )
            )
            session.commit()
            next_state["compile_error"] = error_log or "compile failure"
            next_state["retry_count"] = int(next_state.get("retry_count", 0)) + 1
            next_state["messages"] = [*next_state.get("messages", []), f"Publisher: compile failed for {app_id}."]
            return next_state
        except Exception as exc:
            session.execute(
                update(Application)
                .where(Application.id == app_id)
                .values(status="FAILED", updated_at=datetime.now(timezone.utc))
            )
            session.add(
                PipelineLog(
                    application_id=app_id,
                    agent_name="publisher",
                    action="compile_exception",
                    message=str(exc)[:5000],
                )
            )
            session.commit()
            next_state["compile_error"] = str(exc)
            next_state["retry_count"] = int(next_state.get("retry_count", 0)) + 1
            next_state["messages"] = [*next_state.get("messages", []), f"Publisher: exception for {app_id}."]
            return next_state


def after_publisher(state: HiveState) -> str:
    has_error = bool(state.get("compile_error"))
    retries = int(state.get("retry_count", 0))
    max_retries = int(state.get("max_retries", 3))
    if has_error and retries <= max_retries:
        return "healer"
    return "supervisor"
