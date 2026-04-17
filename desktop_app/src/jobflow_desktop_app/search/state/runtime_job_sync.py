from __future__ import annotations

import json
from typing import Any

from ..run_state import (
    analysis_completed,
    extract_match_score,
    job_identity_key,
    job_item_key,
    merge_job_item,
)


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
    for jobs in job_lists:
        if not isinstance(jobs, list):
            continue
        for item in jobs:
            if not isinstance(item, dict):
                continue
            key = job_item_key(item) or job_identity_key(item)
            if not key:
                continue
            existing = merged.get(key)
            merged[key] = merge_job_item(existing or {}, item) if existing else dict(item)
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


def build_runtime_bucket_rows(
    *,
    bucket: str,
    items: list[dict[str, Any]],
    job_ids: dict[str, int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = job_item_key(item) or job_identity_key(item)
        if not key:
            continue
        rows.append(
            {
                "job_id": job_ids.get(key),
                "job_key": key,
                "canonical_url": str(item.get("canonicalUrl") or item.get("url") or "").strip(),
                "source_url": str(item.get("url") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "company_name": str(item.get("company") or "").strip(),
                "location_text": str(item.get("location") or "").strip(),
                "date_found": str(item.get("dateFound") or "").strip(),
                "match_score": extract_match_score(item.get("analysis")),
                "analysis_completed": analysis_completed(item.get("analysis")),
                "recommended": bool((item.get("analysis") or {}).get("recommend")),
                "pending_resume": bucket == "resume_pending",
                "job_json": json.dumps(item, ensure_ascii=False),
            }
        )
    return rows


def write_runtime_job_buckets(
    *,
    search_run_id: int,
    candidate_id: int,
    buckets: dict[str, list[dict[str, Any]]],
    jobs_repo: Any,
    analyses_repo: Any,
    run_jobs_repo: Any,
) -> None:
    jobs_by_key = merge_runtime_jobs(buckets.values())
    job_ids = persist_runtime_jobs(
        jobs_by_key=jobs_by_key,
        jobs_repo=jobs_repo,
        analyses_repo=analyses_repo,
    )
    for bucket, items in buckets.items():
        run_jobs_repo.replace_bucket(
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            job_bucket=bucket,
            rows=build_runtime_bucket_rows(
                bucket=bucket,
                items=items,
                job_ids=job_ids,
            ),
        )


__all__ = [
    "build_runtime_bucket_rows",
    "merge_runtime_jobs",
    "persist_runtime_jobs",
    "write_runtime_job_buckets",
]
