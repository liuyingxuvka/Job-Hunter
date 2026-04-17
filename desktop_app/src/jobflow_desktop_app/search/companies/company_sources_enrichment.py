from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from ..output.final_output import (
    canonical_job_url,
    infer_region_tag,
    infer_source_quality,
    normalize_job_url,
)
from ..run_state import analysis_completed, job_item_key, merge_job_item
from .sources_fetchers import fetch_text, to_number
from .sources_helpers import (
    dedupe_jobs_by_normalized_url,
    extract_all_json_ld_job_postings,
    extract_apply_url_from_html,
    extract_fallback_job_title_from_html,
    extract_location_from_text,
    job_posting_to_fields,
    sanitize_job_title_candidate,
    strip_html_to_text,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def merge_company_source_jobs(
    existing_jobs: list[Mapping[str, Any]],
    incoming_jobs: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw_job in existing_jobs:
        if not isinstance(raw_job, Mapping):
            continue
        job = dict(raw_job)
        key = job_item_key(job)
        if not key:
            continue
        merged[key] = job
    for raw_job in incoming_jobs:
        if not isinstance(raw_job, Mapping):
            continue
        job = dict(raw_job)
        key = job_item_key(job)
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = job
            continue
        merged[key] = merge_job_item(existing, job)
    return list(merged.values())


def build_found_job_records(
    jobs: list[Mapping[str, Any]],
    *,
    existing_jobs: list[Mapping[str, Any]],
    config: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    existing_by_key = {
        job_item_key(dict(item)): dict(item)
        for item in existing_jobs
        if isinstance(item, Mapping) and job_item_key(dict(item))
    }
    found_records: list[dict[str, Any]] = []
    for raw_job in jobs:
        if not isinstance(raw_job, Mapping):
            continue
        job = dict(raw_job)
        key = job_item_key(job)
        existing = existing_by_key.get(key or "")
        canonical_url = normalize_job_url(job.get("canonicalUrl") or "") or canonical_job_url(job) or normalize_job_url(job.get("url") or "")
        found_records.append(
            {
                "title": str(job.get("title") or "").strip(),
                "company": str(job.get("company") or "").strip(),
                "location": str(job.get("location") or "").strip(),
                "url": normalize_job_url(job.get("url") or ""),
                "canonicalUrl": canonical_url,
                "source": str(job.get("source") or "").strip(),
                "sourceQuality": str(job.get("sourceQuality") or infer_source_quality(job, config)),
                "regionTag": str(job.get("regionTag") or infer_region_tag(job)),
                "fitTrack": str(((existing or {}).get("analysis") or {}).get("fitTrack") or "").strip(),
                "companyTags": list(job.get("companyTags") or []) if isinstance(job.get("companyTags"), list) else [],
                "alreadyAnalyzed": analysis_completed((existing or {}).get("analysis")),
            }
        )
    return dedupe_jobs_by_normalized_url(found_records)


def enrich_selected_jobs_with_details(
    jobs: list[Mapping[str, Any]],
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
    detail_fetch_cap: int,
    already_fetched_count: int,
    progress_callback: Callable[[str], None] | None = None,
    detail_enricher: Callable[..., dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    if not jobs:
        return [], already_fetched_count
    enriched_jobs: list[dict[str, Any]] = []
    fetched_count = max(0, already_fetched_count)
    effective_detail_enricher = detail_enricher or enrich_job_with_details
    for raw_job in jobs:
        job = dict(raw_job) if isinstance(raw_job, Mapping) else {}
        if not job:
            continue
        should_fetch = False
        if detail_fetch_cap <= 0 or fetched_count < detail_fetch_cap:
            should_fetch = job_needs_detail_fetch(job)
        if should_fetch:
            if progress_callback is not None:
                progress_callback(
                    f"Python JD fetch: {str(job.get('company') or '').strip()} | {str(job.get('title') or '').strip()}"
                )
            job = effective_detail_enricher(
                job,
                config=config,
                timeout_seconds=timeout_seconds,
            )
            fetched_count += 1
        enriched_jobs.append(job)
    return enriched_jobs, fetched_count


def job_needs_detail_fetch(job: Mapping[str, Any]) -> bool:
    jd = job.get("jd")
    if isinstance(jd, Mapping) and str(jd.get("text") or jd.get("rawText") or "").strip():
        return False
    summary = str(job.get("summary") or "").strip()
    source_type = str(job.get("sourceType") or "").strip().lower()
    if source_type == "company_search":
        return True
    return len(summary) < 120


def enrich_job_with_details(
    job: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
    details_fetcher: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    merged = dict(job)
    url = normalize_job_url(merged.get("url") or "")
    if not url:
        return merged
    effective_details_fetcher = details_fetcher or fetch_job_details
    details = effective_details_fetcher(
        url,
        config=config,
        timeout_seconds=timeout_seconds,
    )
    extracted = dict(details.get("extracted") or {}) if isinstance(details.get("extracted"), Mapping) else {}
    merged["jd"] = {
        "fetchedAt": str(details.get("fetchedAt") or now_iso()),
        "ok": bool(details.get("ok")),
        "status": int(to_number(details.get("status"), 0)),
        "finalUrl": str(details.get("finalUrl") or url).strip(),
        "redirected": bool(details.get("redirected")),
        "text": str(extracted.get("description") or "").strip(),
        "rawText": str(details.get("rawText") or "").strip(),
        "applyUrl": normalize_job_url(details.get("applyUrl") or ""),
    }
    title = sanitize_job_title_candidate(extracted.get("title") or "")
    if title:
        merged["title"] = title
    company = str(extracted.get("company") or "").strip()
    if company:
        merged["company"] = company
    location = str(extracted.get("location") or "").strip()
    if location:
        merged["location"] = location
    elif not str(merged.get("location") or "").strip():
        location_hint = str(details.get("locationHint") or "").strip()
        if location_hint:
            merged["location"] = location_hint
    date_posted = str(extracted.get("datePosted") or "").strip()
    if date_posted:
        merged["datePosted"] = date_posted
    description = str(extracted.get("description") or "").strip()
    if description:
        merged["summary"] = description[:400].strip()
    elif not str(merged.get("summary") or "").strip():
        raw_text = str(details.get("rawText") or "").strip()
        if raw_text:
            merged["summary"] = raw_text[:400].strip()
    return merged


def fetch_job_details(
    url: str,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> dict[str, Any]:
    normalized_url = normalize_job_url(url)
    if not normalized_url:
        return {
            "ok": False,
            "status": 0,
            "contentType": "",
            "finalUrl": str(url or "").strip(),
            "redirected": False,
            "extracted": {},
            "rawText": "",
            "applyUrl": "",
            "fetchedAt": now_iso(),
            "locationHint": "",
            "error": "Missing job URL.",
        }
    try:
        html, final_url = fetch_text(
            normalized_url,
            config=config,
            timeout_seconds=timeout_seconds,
        )
        postings = extract_all_json_ld_job_postings(html)
        fields = job_posting_to_fields(postings[0]) if postings else {}
        text = strip_html_to_text(html)
        extracted_title = sanitize_job_title_candidate(fields.get("title") or "") or extract_fallback_job_title_from_html(
            html,
            final_url or normalized_url,
        )
        apply_url = extract_apply_url_from_html(html, final_url or normalized_url)
        location_hint = str(fields.get("location") or "").strip() or extract_location_from_text(text)
        extracted = (
            {
                "title": extracted_title,
                "company": str(fields.get("company") or "").strip(),
                "location": str(fields.get("location") or "").strip(),
                "datePosted": str(fields.get("datePosted") or "").strip(),
                "description": str(fields.get("description") or "").strip(),
            }
            if extracted_title or fields
            else {}
        )
        return {
            "ok": True,
            "status": 200,
            "contentType": "text/html",
            "finalUrl": final_url or normalized_url,
            "redirected": bool(final_url and normalize_job_url(final_url) != normalized_url),
            "extracted": extracted,
            "rawText": text[:20000],
            "applyUrl": apply_url,
            "fetchedAt": now_iso(),
            "locationHint": location_hint,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "contentType": "",
            "finalUrl": normalized_url,
            "redirected": False,
            "extracted": {},
            "rawText": "",
            "applyUrl": "",
            "fetchedAt": now_iso(),
            "locationHint": "",
            "error": str(exc),
        }


__all__ = [
    "build_found_job_records",
    "enrich_job_with_details",
    "enrich_selected_jobs_with_details",
    "fetch_job_details",
    "job_needs_detail_fetch",
    "merge_company_source_jobs",
]
