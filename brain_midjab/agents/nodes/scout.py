from __future__ import annotations

from pathlib import Path

import pandas as pd

from agents.data_factory import process_raw_job
from agents.discovery_engine import DiscoveryEngine
from agents.state import HiveState


def scout_node(state: HiveState) -> HiveState:
    next_state = dict(state)
    inserted_or_updated: list[str] = []

    engine = DiscoveryEngine()
    engine.run_broad_scan()

    csv_path = Path("outputs/raw_jobs.csv")
    if not csv_path.exists():
        next_state["new_job_ids"] = []
        next_state["discovery_done"] = True
        next_state["messages"] = [*next_state.get("messages", []), "Scout: no raw jobs file found."]
        return next_state

    jobs_df = pd.read_csv(csv_path)
    for row in jobs_df.to_dict(orient="records"):
        source = str(row.get("site", "") or row.get("source", "") or "jobspy")
        result = process_raw_job(row, source=source)
        if result.get("status") in {"inserted", "updated"} and result.get("id"):
            inserted_or_updated.append(str(result["id"]))

    next_state["new_job_ids"] = inserted_or_updated
    next_state["discovery_done"] = True
    next_state["messages"] = [
        *next_state.get("messages", []),
        f"Scout: ingested {len(inserted_or_updated)} jobs.",
    ]
    return next_state
