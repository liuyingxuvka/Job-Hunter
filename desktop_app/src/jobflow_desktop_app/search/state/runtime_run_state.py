from __future__ import annotations

import json
from typing import Any


class SearchRunStateStore:
    def __init__(
        self,
        *,
        search_runs,
    ) -> None:
        self.search_runs = search_runs

    def create_run(
        self,
        *,
        candidate_id: int,
        run_dir,
        status: str,
        current_stage: str,
        started_at: str,
    ) -> int:
        return self.search_runs.create_run(
            candidate_id=candidate_id,
            run_dir=str(run_dir),
            status=status,
            current_stage=current_stage,
            started_at=started_at,
        )

    def update_progress(
        self,
        search_run_id: int | None,
        *,
        status: str,
        stage: str,
        message: str = "",
        last_event: str = "",
        started_at: str = "",
    ) -> None:
        if search_run_id is None:
            return
        self.search_runs.update_progress(
            search_run_id,
            status=status,
            current_stage=stage,
            last_message=message,
            last_event=last_event,
            started_at=started_at,
        )
        if status == "error":
            self.search_runs.mark_error(search_run_id, message or last_event)

    def update_configs(
        self,
        search_run_id: int | None,
        *,
        runtime_config: dict[str, Any] | None = None,
    ) -> None:
        if search_run_id is None:
            return
        self.search_runs.update_configs(
            search_run_id,
            config_json=(
                json.dumps(runtime_config, ensure_ascii=False, indent=2)
                if runtime_config is not None
                else None
            ),
        )

    def latest_run(self, candidate_id: int):
        return self.search_runs.latest_for_candidate(candidate_id)

    def recent_runs(self, candidate_id: int, *, limit: int = 5):
        return self.search_runs.recent_for_candidate(candidate_id, limit=limit)

    def all_runs(self, candidate_id: int):
        return self.search_runs.all_for_candidate(candidate_id)

    def load_latest_progress_payload(self, candidate_id: int) -> dict[str, Any] | None:
        latest_run = self.latest_run(candidate_id)
        if latest_run is None:
            return None
        return {
            "status": latest_run.status or "idle",
            "stage": latest_run.current_stage or "idle",
            "message": latest_run.last_message,
            "lastEvent": latest_run.last_event,
            "startedAt": latest_run.started_at,
            "updatedAt": latest_run.updated_at,
        }

    def load_run_config(
        self,
        *,
        candidate_id: int,
    ) -> dict[str, Any]:
        latest_run = self.latest_run(candidate_id)
        if latest_run is None:
            return {}
        return self.search_runs.load_config_payload(latest_run.search_run_id)


__all__ = ["SearchRunStateStore"]
