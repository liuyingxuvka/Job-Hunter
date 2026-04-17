from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from ..output.final_output import normalize_job_url
from ..run_state import analysis_completed


def get_company_cooldown_until(
    adaptive_search: Mapping[str, Any] | None,
    *,
    jobs_found_count: int,
    new_jobs_count: int,
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    cooldown_base_days = max(
        1,
        int(_to_number(adaptive_search, "cooldownBaseDays", default=7)),
    )
    no_jobs_days = cooldown_base_days
    some_jobs_no_new_days = cooldown_base_days
    with_new_days = max(1, cooldown_base_days // 3)
    if new_jobs_count > 0:
        days = with_new_days
    elif jobs_found_count > 0:
        days = some_jobs_no_new_days
    else:
        days = no_jobs_days
    return (current + timedelta(days=days)).replace(microsecond=0).isoformat()


def reconcile_company_pipeline_state_in_memory(
    *,
    companies: list[dict[str, Any]],
    jobs: list[Mapping[str, Any]],
    config: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, int | bool]:
    if not companies:
        return {"changed": False, "pendingCompanies": 0}

    existing_by_url = _build_existing_by_url(jobs)
    changed = False
    pending_companies = 0
    current = now or datetime.now(timezone.utc)
    adaptive_search = config.get("adaptiveSearch") if isinstance(config, Mapping) else {}

    for item in companies:
        if not isinstance(item, dict):
            continue
        snapshot_urls = _normalized_company_job_urls(item.get("snapshotJobUrls"))
        known_job_urls = _normalized_company_job_urls(item.get("knownJobUrls"))
        pending_urls: set[str] = set()
        for url in snapshot_urls:
            job = existing_by_url.get(url)
            if job is None or not analysis_completed(job.get("analysis")):
                pending_urls.add(url)
        for url in known_job_urls:
            job = existing_by_url.get(url)
            if job is not None and not analysis_completed(job.get("analysis")):
                pending_urls.add(url)

        pending_analysis_count = len(pending_urls)
        previous_pending = int(_coerce_int(item.get("snapshotPendingAnalysisCount")))
        if pending_analysis_count > 0:
            pending_companies += 1
            if previous_pending != pending_analysis_count:
                item["snapshotPendingAnalysisCount"] = pending_analysis_count
                changed = True
            if "cooldownUntil" in item:
                del item["cooldownUntil"]
                changed = True
            continue

        if "snapshotPendingAnalysisCount" in item:
            del item["snapshotPendingAnalysisCount"]
            changed = True

        if item.get("snapshotComplete") is True:
            next_cooldown = get_company_cooldown_until(
                adaptive_search if isinstance(adaptive_search, Mapping) else {},
                jobs_found_count=max(
                    0,
                    int(_coerce_int(item.get("lastJobsFoundCount"), default=len(snapshot_urls))),
                ),
                new_jobs_count=max(0, int(_coerce_int(item.get("lastNewJobsCount")))),
                now=current,
            )
            if str(item.get("cooldownUntil") or "").strip() != next_cooldown:
                item["cooldownUntil"] = next_cooldown
                changed = True
        elif "cooldownUntil" in item:
            del item["cooldownUntil"]
            changed = True

    return {"changed": changed, "pendingCompanies": pending_companies}


def _normalized_company_job_urls(raw_urls: object) -> list[str]:
    if not isinstance(raw_urls, list):
        return []
    normalized: list[str] = []
    for item in raw_urls:
        value = normalize_job_url(item)
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _build_existing_by_url(jobs: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    existing: dict[str, Mapping[str, Any]] = {}
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        for candidate in (
            normalize_job_url(job.get("url") or ""),
            normalize_job_url(job.get("canonicalUrl") or ""),
        ):
            if candidate and candidate not in existing:
                existing[candidate] = job
    return existing


def _coerce_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        text = str(value).strip()
        return int(float(text)) if text else default
    except (TypeError, ValueError):
        return default


def _to_number(
    payload: Mapping[str, Any] | None,
    key: str,
    *,
    default: int,
) -> int:
    if not isinstance(payload, Mapping):
        return default
    return _coerce_int(payload.get(key), default=default)


__all__ = [
    "get_company_cooldown_until",
    "reconcile_company_pipeline_state_in_memory",
]
