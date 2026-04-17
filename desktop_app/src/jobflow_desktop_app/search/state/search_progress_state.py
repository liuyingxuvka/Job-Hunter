from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .runtime_run_locator import candidate_id_from_run_dir, search_run_repository_for_run_dir


@dataclass(frozen=True)
class SearchStats:
    discovered_job_count: int = 0
    discovered_company_count: int = 0
    scored_job_count: int = 0
    recommended_job_count: int = 0
    pending_resume_count: int = 0
    candidate_company_pool_count: int = 0
    signal_hit_job_count: int = 0
    main_discovered_job_count: int = 0
    main_scored_job_count: int = 0
    displayable_result_count: int = 0
    main_pending_analysis_count: int = 0


@dataclass(frozen=True)
class SearchProgress:
    status: str = "idle"
    stage: str = "idle"
    message: str = ""
    last_event: str = ""
    started_at: str = ""
    updated_at: str = ""
    elapsed_seconds: int = 0


def load_search_progress_from_run_dir(run_dir: Path) -> SearchProgress:
    candidate_id = candidate_id_from_run_dir(run_dir)
    search_runs = search_run_repository_for_run_dir(run_dir)
    if candidate_id is None or search_runs is None:
        return SearchProgress()
    snapshot = search_runs.latest_for_candidate(candidate_id)
    if snapshot is None:
        return SearchProgress()
    started_at = str(snapshot.started_at or "").strip()
    elapsed_seconds = 0
    if started_at:
        try:
            started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            now_dt = datetime.now(timezone.utc)
            elapsed_seconds = max(0, int((now_dt - started_dt).total_seconds()))
        except Exception:
            elapsed_seconds = 0
    return SearchProgress(
        status=str(snapshot.status or "idle").strip() or "idle",
        stage=str(snapshot.current_stage or "idle").strip() or "idle",
        message=str(snapshot.last_message or "").strip(),
        last_event=str(snapshot.last_event or "").strip(),
        started_at=started_at,
        updated_at=str(snapshot.updated_at or "").strip(),
        elapsed_seconds=elapsed_seconds,
    )


def write_search_progress(
    run_dir: Path,
    *,
    status: str,
    stage: str,
    message: str = "",
    last_event: str = "",
    started_at: str = "",
) -> None:
    candidate_id = candidate_id_from_run_dir(run_dir)
    search_runs = search_run_repository_for_run_dir(run_dir)
    if candidate_id is None or search_runs is None:
        return
    snapshot = search_runs.latest_for_candidate(candidate_id)
    if snapshot is None:
        return
    search_runs.update_progress(
        snapshot.search_run_id,
        status=str(status or "idle").strip() or "idle",
        current_stage=str(stage or "idle").strip() or "idle",
        last_message=str(message or "").strip(),
        last_event=str(last_event or "").strip(),
        started_at=str(started_at or "").strip(),
    )
__all__ = [
    "SearchProgress",
    "SearchStats",
    "load_search_progress_from_run_dir",
    "write_search_progress",
]
