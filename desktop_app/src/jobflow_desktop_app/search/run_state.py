from __future__ import annotations

from pathlib import Path


def job_identity_key(item: dict) -> str:
    title = str(item.get("title") or "").strip().casefold()
    company = str(item.get("company") or "").strip().casefold()
    date_found = str(item.get("dateFound") or "").strip()
    return f"{title}|{company}|{date_found}"


def job_item_key(item: dict) -> str:
    url = str(item.get("url") or item.get("canonicalUrl") or "").strip()
    if url:
        return url.casefold()
    return job_identity_key(item)


def merge_job_item(existing: dict, incoming: dict) -> dict:
    merged = dict(existing)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged.get(key) or {})
            for nested_key, nested_value in value.items():
                if nested_value not in ("", None, [], {}):
                    nested[nested_key] = nested_value
            merged[key] = nested
            continue
        if value not in ("", None, [], {}):
            merged[key] = value
    return merged


def extract_overall_score(analysis: object) -> int | None:
    if not isinstance(analysis, dict):
        return None
    raw_score = analysis.get("overallScore")
    if isinstance(raw_score, bool):
        return None
    if isinstance(raw_score, int):
        return raw_score
    if isinstance(raw_score, float) and raw_score.is_integer():
        return int(raw_score)
    try:
        text = str(raw_score or "").strip()
        if text:
            return int(text)
    except (TypeError, ValueError):
        pass
    return None


def extract_match_score(analysis: object) -> int | None:
    return extract_overall_score(analysis)


def analysis_completed(analysis: object) -> bool:
    if not isinstance(analysis, dict):
        return False
    if bool(analysis.get("prefilterRejected")):
        return True
    return extract_overall_score(analysis) is not None


def _merge_jobs_into_map(
    merged_jobs: dict[str, dict],
    jobs: list[dict],
) -> dict[str, dict]:
    for item in jobs:
        if not isinstance(item, dict):
            continue
        key = job_item_key(item)
        if not key:
            continue
        existing = merged_jobs.get(key)
        if existing is None:
            merged_jobs[key] = dict(item)
            continue
        merged_jobs[key] = merge_job_item(existing, item)
    return merged_jobs


def merge_job_items_from_job_lists(*job_lists: list[dict]) -> list[dict]:
    merged_jobs: dict[str, dict] = {}
    for jobs in job_lists:
        merged_jobs = _merge_jobs_into_map(
            merged_jobs,
            jobs if isinstance(jobs, list) else [],
        )
    return list(merged_jobs.values())


def collect_resume_pending_jobs_from_job_lists(*job_lists: list[dict]) -> list[dict]:
    pending_jobs: list[dict] = []
    for item in merge_job_items_from_job_lists(*job_lists):
        if not isinstance(item, dict):
            continue
        job = dict(item)
        job_url = str(job.get("url") or job.get("canonicalUrl") or "").strip()
        if not job_url:
            continue
        job["url"] = job_url
        if analysis_completed(job.get("analysis")):
            continue
        pending_jobs.append(job)
    pending_jobs.sort(
        key=lambda item: (
            str(item.get("dateFound") or ""),
            str(item.get("company") or "").casefold(),
            str(item.get("title") or "").casefold(),
            str(item.get("url") or "").casefold(),
        )
    )
    return pending_jobs


def normalize_resume_pending_jobs(
    jobs: list[dict],
    run_dir: Path,
    include_found_details: bool = False,
) -> list[dict]:
    del run_dir
    del include_found_details
    return collect_resume_pending_jobs_from_job_lists(
        jobs if isinstance(jobs, list) else [],
    )


def merge_resume_pending_job_lists(
    run_dir: Path,
    *job_lists: list[dict],
    include_found_details: bool = False,
) -> list[dict]:
    del run_dir
    del include_found_details
    return collect_resume_pending_jobs_from_job_lists(*job_lists)


__all__ = [
    "analysis_completed",
    "collect_resume_pending_jobs_from_job_lists",
    "extract_overall_score",
    "extract_match_score",
    "job_identity_key",
    "job_item_key",
    "merge_job_item",
    "merge_resume_pending_job_lists",
    "normalize_resume_pending_jobs",
]
