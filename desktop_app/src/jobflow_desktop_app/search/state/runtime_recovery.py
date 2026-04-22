from __future__ import annotations

from typing import Any


INTERRUPTED_SEARCH_MESSAGE = (
    "Search interrupted because the desktop app closed before this search finished."
)
INTERRUPTED_SEARCH_EVENT = (
    "Recovered stale running search from a previous desktop session."
)


def recover_interrupted_search_runs(
    runtime_mirror: Any,
    *,
    candidate_id: int | None = None,
    message: str = INTERRUPTED_SEARCH_MESSAGE,
    last_event: str = INTERRUPTED_SEARCH_EVENT,
) -> list[int]:
    if runtime_mirror is None:
        return []
    search_runs = getattr(runtime_mirror, "search_runs", None)
    if search_runs is None or not hasattr(search_runs, "running_runs"):
        return []
    snapshots = search_runs.running_runs(candidate_id=candidate_id)
    recovered_ids: list[int] = []
    for snapshot in snapshots:
        search_run_id = int(getattr(snapshot, "search_run_id", 0) or 0)
        if search_run_id <= 0:
            continue
        try:
            runtime_mirror.refresh_counts(search_run_id=search_run_id)
        except Exception:
            pass
        runtime_mirror.update_progress(
            search_run_id,
            status="cancelled",
            stage="done",
            message=str(message or "").strip() or INTERRUPTED_SEARCH_MESSAGE,
            last_event=str(last_event or "").strip() or INTERRUPTED_SEARCH_EVENT,
            started_at=str(getattr(snapshot, "started_at", "") or "").strip(),
        )
        recovered_ids.append(search_run_id)
    return recovered_ids


__all__ = [
    "INTERRUPTED_SEARCH_EVENT",
    "INTERRUPTED_SEARCH_MESSAGE",
    "recover_interrupted_search_runs",
]
