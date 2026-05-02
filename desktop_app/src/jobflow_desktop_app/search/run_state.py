from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .state.work_unit_state import is_abandoned, is_suspended_for_run, normalize_work_unit_state


def normalize_job_key_url(raw_url: object) -> str:
    text = str(raw_url or "").strip()
    if not text:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", text, flags=re.IGNORECASE):
        return ""
    try:
        parts = urlsplit(text)
    except Exception:
        return ""
    scheme = (parts.scheme or "https").lower()
    netloc = (parts.netloc or "").lower()
    if not netloc:
        return ""
    path = parts.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    path = re.sub(r"/{2,}", "/", path)
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(("utm_", "fbclid", "gclid"))
    ]
    query = urlencode(query_items, doseq=True)
    return urlunsplit((scheme, netloc, path.rstrip("/") or "/", query, ""))


def canonical_job_item_url(item: dict) -> str:
    return normalize_job_key_url(item.get("canonicalUrl")) or normalize_job_key_url(item.get("url"))


def job_identity_key(item: dict) -> str:
    title = str(item.get("title") or "").strip().casefold()
    company = str(item.get("company") or "").strip().casefold()
    date_found = str(item.get("dateFound") or "").strip()
    return f"{title}|{company}|{date_found}"


def _normalize_structural_key_text(value: object) -> str:
    return re.sub(r"[\s\-_.,;:(){}\[\]<>]+", " ", str(value or "").strip()).casefold().strip()


def job_structural_key(item: dict) -> str:
    title = _normalize_structural_key_text(item.get("title"))
    company = _normalize_structural_key_text(item.get("company"))
    location = _normalize_structural_key_text(item.get("location"))
    if not title or not company or not location:
        return ""
    return f"struct:{company}|{title}|{location}"


def job_item_key(item: dict) -> str:
    url = canonical_job_item_url(item)
    if url:
        return url.casefold()
    return job_identity_key(item)


def job_item_aliases(item: dict) -> tuple[str, ...]:
    aliases: list[str] = []
    for key in (job_item_key(item), job_structural_key(item), job_identity_key(item)):
        normalized = str(key or "").strip()
        if normalized and normalized not in aliases:
            aliases.append(normalized)
    return tuple(aliases)


def merge_job_item(existing: dict, incoming: dict) -> dict:
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "processingState":
            normalized_state = normalize_work_unit_state(value)
            if normalized_state:
                merged[key] = normalized_state
            else:
                merged.pop(key, None)
            continue
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
    alias_to_key: dict[str, str] = {}
    for existing_key, existing_item in merged_jobs.items():
        if not isinstance(existing_item, dict):
            continue
        for alias in job_item_aliases(existing_item):
            alias_to_key.setdefault(alias, existing_key)
    for item in jobs:
        if not isinstance(item, dict):
            continue
        key = job_item_key(item)
        if not key:
            continue
        aliases = job_item_aliases(item)
        merge_key = next(
            (alias_to_key[alias] for alias in aliases if alias in alias_to_key),
            key,
        )
        existing = merged_jobs.get(merge_key)
        if existing is None:
            merged_jobs[merge_key] = dict(item)
            for alias in aliases:
                alias_to_key.setdefault(alias, merge_key)
            continue
        merged = merge_job_item(existing, item)
        merged_jobs[merge_key] = merged
        for alias in (*aliases, *job_item_aliases(merged)):
            alias_to_key.setdefault(alias, merge_key)
    return merged_jobs


def merge_job_items_from_job_lists(*job_lists: list[dict]) -> list[dict]:
    merged_jobs: dict[str, dict] = {}
    for jobs in job_lists:
        merged_jobs = _merge_jobs_into_map(
            merged_jobs,
            jobs if isinstance(jobs, list) else [],
        )
    return list(merged_jobs.values())


def collect_resume_pending_jobs_from_job_lists(
    *job_lists: list[dict],
    current_run_id: int | None = None,
) -> list[dict]:
    return _collect_resume_pending_jobs_from_job_lists(current_run_id, *job_lists)


def _collect_resume_pending_jobs_from_job_lists(
    current_run_id: int | None,
    *job_lists: list[dict],
) -> list[dict]:
    pending_jobs: list[dict] = []
    for item in merge_job_items_from_job_lists(*job_lists):
        if not isinstance(item, dict):
            continue
        job = dict(item)
        job_url = canonical_job_item_url(job)
        if not job_url:
            continue
        job["url"] = job_url
        if analysis_completed(job.get("analysis")):
            continue
        processing_state = job.get("processingState")
        if is_abandoned(processing_state):
            continue
        if is_suspended_for_run(processing_state, current_run_id):
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
    current_run_id: int | None = None,
) -> list[dict]:
    del run_dir
    del include_found_details
    return _collect_resume_pending_jobs_from_job_lists(
        current_run_id,
        jobs if isinstance(jobs, list) else [],
    )


def merge_resume_pending_job_lists(
    run_dir: Path,
    *job_lists: list[dict],
    include_found_details: bool = False,
    current_run_id: int | None = None,
) -> list[dict]:
    del run_dir
    del include_found_details
    return _collect_resume_pending_jobs_from_job_lists(current_run_id, *job_lists)


__all__ = [
    "analysis_completed",
    "canonical_job_item_url",
    "collect_resume_pending_jobs_from_job_lists",
    "extract_overall_score",
    "extract_match_score",
    "job_identity_key",
    "job_item_key",
    "job_structural_key",
    "merge_job_item",
    "merge_resume_pending_job_lists",
    "normalize_resume_pending_jobs",
]
