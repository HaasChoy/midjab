from __future__ import annotations

from typing import TypedDict


class HiveState(TypedDict):
    # Identity
    user_id: str
    resume_id: str

    # Routing / orchestration
    phase: str
    discovery_done: bool
    only_mode: str | None
    skip_discovery: bool
    min_match_score: float
    llm_model: str

    # Batches
    new_job_ids: list[str]
    pending_app_ids: list[str]

    # Compile loop
    current_app_id: str | None
    compile_error: str | None
    retry_count: int
    max_retries: int

    # Observability
    messages: list[str]
