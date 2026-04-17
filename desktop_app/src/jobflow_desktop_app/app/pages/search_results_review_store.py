from __future__ import annotations

import json
from typing import Any, Callable

from ...db.repositories.search_runtime import JobReviewStateRepository


def status_store_key(candidate_id: int | None) -> str:
    normalized_candidate_id = int(candidate_id or 0)
    return f"search_results_statuses_candidate_{normalized_candidate_id}"


def hidden_store_key(candidate_id: int | None) -> str:
    normalized_candidate_id = int(candidate_id or 0)
    return f"search_results_hidden_candidate_{normalized_candidate_id}"


def load_review_state(
    settings: Any,
    candidate_id: int,
    normalize_status_code: Callable[[str], str | None],
) -> tuple[dict[str, str], set[str]]:
    review_states = JobReviewStateRepository(settings.database)
    statuses, hidden = review_states.load_candidate_review_state(int(candidate_id))
    if statuses or hidden:
        return statuses, hidden

    raw_statuses = settings.get_value(status_store_key(candidate_id), "{}")
    raw_hidden = settings.get_value(hidden_store_key(candidate_id), "[]")
    try:
        status_map = json.loads(raw_statuses)
    except Exception:
        status_map = {}
    try:
        hidden_list = json.loads(raw_hidden)
    except Exception:
        hidden_list = []

    normalized_statuses: dict[str, str] = {}
    if isinstance(status_map, dict):
        for key, value in status_map.items():
            status_code = normalize_status_code(str(value))
            if status_code:
                normalized_statuses[str(key)] = status_code

    hidden_job_keys = (
        {str(item) for item in hidden_list if str(item).strip()}
        if isinstance(hidden_list, list)
        else set()
    )
    if normalized_statuses or hidden_job_keys:
        review_states.replace_candidate_review_state(
            candidate_id=int(candidate_id),
            status_by_job_key=normalized_statuses,
            hidden_job_keys=hidden_job_keys,
        )
        settings.delete_value(status_store_key(candidate_id))
        settings.delete_value(hidden_store_key(candidate_id))
    return normalized_statuses, hidden_job_keys


def save_review_state(
    settings: Any,
    candidate_id: int | None,
    status_by_job_key: dict[str, str],
    hidden_job_keys: set[str],
) -> None:
    if candidate_id is None:
        return
    JobReviewStateRepository(settings.database).replace_candidate_review_state(
        candidate_id=int(candidate_id),
        status_by_job_key={
            str(key).strip(): str(value or "").strip()
            for key, value in status_by_job_key.items()
            if str(key).strip()
        },
        hidden_job_keys={
            str(item).strip()
            for item in hidden_job_keys
            if str(item).strip()
        },
    )
    settings.delete_value(status_store_key(candidate_id))
    settings.delete_value(hidden_store_key(candidate_id))


__all__ = [
    "hidden_store_key",
    "load_review_state",
    "save_review_state",
    "status_store_key",
]
