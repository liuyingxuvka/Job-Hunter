from __future__ import annotations

from typing import Any

from ..run_state import job_identity_key, job_item_key, merge_job_items_from_job_lists


def _search_profile_id_from_job(item: dict[str, Any]) -> int | None:
    analysis = item.get("analysis")
    if not isinstance(analysis, dict):
        return None
    bound_target_role = analysis.get("boundTargetRole")
    if not isinstance(bound_target_role, dict):
        return None
    raw_value = bound_target_role.get("profileId")
    if isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, int):
        return raw_value if raw_value > 0 else None
    if isinstance(raw_value, float) and raw_value.is_integer():
        value = int(raw_value)
        return value if value > 0 else None
    try:
        text = str(raw_value or "").strip()
        value = int(text)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def merge_runtime_jobs(job_lists: Any) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    normalized_lists = [
        [dict(item) for item in jobs if isinstance(item, dict)]
        for jobs in job_lists
        if isinstance(jobs, list)
    ]
    for item in merge_job_items_from_job_lists(*normalized_lists):
        key = job_item_key(item) or job_identity_key(item)
        if not key:
            continue
        merged[key] = dict(item)
    return merged


def persist_runtime_jobs(
    *,
    jobs_by_key: dict[str, dict[str, Any]],
    jobs_repo: Any,
    analyses_repo: Any,
) -> dict[str, int]:
    job_ids: dict[str, int] = {}
    for key, item in jobs_by_key.items():
        job_id = jobs_repo.upsert_job(item)
        if job_id is not None:
            job_ids[key] = job_id
    for key, item in jobs_by_key.items():
        job_id = job_ids.get(key)
        if job_id is None:
            continue
        analysis = item.get("analysis")
        if not isinstance(analysis, dict) or not analysis:
            continue
        profile_id = _search_profile_id_from_job(item)
        if profile_id is None:
            continue
        analyses_repo.upsert_analysis(
            job_id=job_id,
            search_profile_id=profile_id,
            analysis=analysis,
        )
    return job_ids


def write_runtime_job_pool(
    *,
    search_run_id: int,
    candidate_id: int,
    job_lists: list[list[dict[str, Any]]],
    jobs_repo: Any,
    analyses_repo: Any,
    candidate_jobs_repo: Any | None,
) -> None:
    jobs_by_key = merge_runtime_jobs(job_lists)
    job_ids = persist_runtime_jobs(
        jobs_by_key=jobs_by_key,
        jobs_repo=jobs_repo,
        analyses_repo=analyses_repo,
    )
    if candidate_jobs_repo is not None and hasattr(candidate_jobs_repo, "upsert_runtime_jobs"):
        candidate_jobs_repo.upsert_runtime_jobs(
            candidate_id=candidate_id,
            search_run_id=search_run_id,
            jobs_by_key=jobs_by_key,
            job_ids=job_ids,
        )


__all__ = [
    "merge_runtime_jobs",
    "persist_runtime_jobs",
    "write_runtime_job_pool",
]
