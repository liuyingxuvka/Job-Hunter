from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError

from ...ai.client import build_json_schema_request, build_text_input_messages, parse_response_json
from ...prompt_assets import load_prompt_asset
from ..output.final_output import (
    canonical_job_url,
    infer_region_tag,
    infer_source_quality,
    normalize_job_url,
)
from ..analysis.service import ResponseRequestClient
from ..run_state import analysis_completed, job_item_key, merge_job_item
from .sources_fetchers import extract_pdf_text_from_bytes, fetch_response, fetch_text, to_number
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

_JOB_DETAIL_RESCUE_PROMPT = load_prompt_asset("search_ranking", "job_detail_rescue_prompt.txt")
_JOB_DETAIL_RESCUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recovered": {"type": "boolean"},
        "isConcreteEmployeeRole": {"type": "boolean"},
        "title": {"type": "string"},
        "company": {"type": "string"},
        "location": {"type": "string"},
        "datePosted": {"type": "string"},
        "description": {"type": "string"},
        "applyUrl": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": [
        "recovered",
        "isConcreteEmployeeRole",
        "title",
        "company",
        "location",
        "datePosted",
        "description",
        "applyUrl",
        "reason",
    ],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_antibot_interstitial_text(text: object) -> bool:
    normalized = " ".join(str(text or "").split()).casefold()
    if not normalized:
        return False
    has_js_gate = "javascript is disabled" in normalized or "this requires javascript" in normalized
    has_robot_gate = (
        "verify that you're not a robot" in normalized
        or "verify that you are not a robot" in normalized
        or "enable javascript and then reload the page" in normalized
    )
    return has_js_gate and has_robot_gate


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
    client: ResponseRequestClient | None,
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
                client=client,
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
    client: ResponseRequestClient | None,
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
    if _job_detail_fetch_needs_ai_rescue(details):
        rescued_details = rescue_job_details_with_ai(
            client,
            config=config,
            job=merged,
            timeout_seconds=timeout_seconds,
            failed_details=details,
        )
        if rescued_details is not None:
            details = rescued_details
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
        "rescuedByAI": bool(details.get("rescuedByAI")),
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


def _job_detail_fetch_needs_ai_rescue(
    details: Mapping[str, Any],
) -> bool:
    if not isinstance(details, Mapping):
        return False
    if bool(details.get("ok")):
        return False
    final_url = normalize_job_url(details.get("finalUrl") or "")
    if not final_url:
        return False
    content_type = str(details.get("contentType") or "").strip().lower()
    if content_type == "application/pdf" or final_url.casefold().endswith(".pdf"):
        return False
    error_text = str(details.get("error") or "").strip().casefold()
    rescue_markers = (
        "anti-bot",
        "anti bot",
        "interstitial",
        "javascript is disabled",
        "not a robot",
    )
    return any(marker in error_text for marker in rescue_markers)


def _detail_rescue_model(config: Mapping[str, Any] | None) -> str:
    analysis = dict(config.get("analysis") or {}) if isinstance(config, Mapping) else {}
    company_discovery = dict(config.get("companyDiscovery") or {}) if isinstance(config, Mapping) else {}
    return str(
        (analysis.get("model") if isinstance(analysis, Mapping) else "")
        or (company_discovery.get("model") if isinstance(company_discovery, Mapping) else "")
        or ""
    ).strip()


def rescue_job_details_with_ai(
    client: ResponseRequestClient | None,
    *,
    config: Mapping[str, Any] | None,
    job: Mapping[str, Any],
    timeout_seconds: int | None,
    failed_details: Mapping[str, Any],
) -> dict[str, Any] | None:
    if client is None or not hasattr(client, "create"):
        return None
    model = _detail_rescue_model(config)
    if not model:
        return None
    job_url = normalize_job_url(job.get("url") or failed_details.get("finalUrl") or "")
    if not job_url:
        return None
    request = build_json_schema_request(
        model=model,
        input_payload=build_text_input_messages(
            _JOB_DETAIL_RESCUE_PROMPT,
            json.dumps(
                {
                    "jobUrl": job_url,
                    "company": str(job.get("company") or "").strip(),
                    "currentTitle": str(job.get("title") or "").strip(),
                    "currentSummary": str(job.get("summary") or "").strip()[:400],
                    "fetchFailure": {
                        "status": int(to_number(failed_details.get("status"), 0)),
                        "contentType": str(failed_details.get("contentType") or "").strip(),
                        "error": str(failed_details.get("error") or "").strip()[:200],
                        "finalUrl": str(failed_details.get("finalUrl") or job_url).strip(),
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
        ),
        schema_name="job_detail_rescue",
        schema=_JOB_DETAIL_RESCUE_SCHEMA,
        use_web_search=True,
    )
    response = client.create(request)
    payload = parse_response_json(response, "Job detail rescue")
    if not bool(payload.get("recovered")) or not bool(payload.get("isConcreteEmployeeRole")):
        return None
    description = str(payload.get("description") or "").strip()
    title = sanitize_job_title_candidate(payload.get("title") or "")
    if not title and not description:
        return None
    final_url = str(failed_details.get("finalUrl") or job_url).strip() or job_url
    apply_url = normalize_job_url(payload.get("applyUrl") or "")
    return {
        "ok": True,
        "status": int(to_number(failed_details.get("status"), 200)) or 200,
        "contentType": "text/ai-rescue",
        "finalUrl": final_url,
        "redirected": bool(failed_details.get("redirected")),
        "extracted": {
            "title": title,
            "company": str(payload.get("company") or "").strip(),
            "location": str(payload.get("location") or "").strip(),
            "datePosted": str(payload.get("datePosted") or "").strip(),
            "description": description,
        },
        "rawText": description[:20000],
        "applyUrl": apply_url,
        "fetchedAt": now_iso(),
        "locationHint": str(payload.get("location") or "").strip(),
        "rescuedByAI": True,
        "error": "",
    }


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
        payload, content_type, final_url = fetch_response(
            normalized_url,
            config=config,
            timeout_seconds=timeout_seconds,
        )
        resolved_url = final_url or normalized_url
        normalized_content_type = str(content_type or "").strip().lower()
        is_pdf = normalized_content_type == "application/pdf" or str(resolved_url).casefold().endswith(".pdf")
        if is_pdf:
            text = extract_pdf_text_from_bytes(payload)
            location_hint = extract_location_from_text(text)
            fallback_title = str(resolved_url.rsplit("/", 1)[-1] or "")
            if fallback_title.casefold().endswith(".pdf"):
                fallback_title = fallback_title[:-4]
            fallback_title = fallback_title.replace("-", " ").replace("_", " ")
            extracted_title = sanitize_job_title_candidate(fallback_title)
            description = text[:12000].strip()
            return {
                "ok": bool(text),
                "status": 200,
                "contentType": normalized_content_type or "application/pdf",
                "finalUrl": resolved_url,
                "redirected": bool(final_url and normalize_job_url(final_url) != normalized_url),
                "extracted": {
                    "title": extracted_title,
                    "company": "",
                    "location": location_hint,
                    "datePosted": "",
                    "description": description,
                },
                "rawText": text[:20000],
                "applyUrl": "",
                "fetchedAt": now_iso(),
                "locationHint": location_hint,
                "error": "" if text else "PDF contains no extractable text.",
            }
        html = payload.decode("utf-8", errors="replace")
        postings = extract_all_json_ld_job_postings(html)
        fields = job_posting_to_fields(postings[0]) if postings else {}
        text = strip_html_to_text(html)
        if not postings and _is_antibot_interstitial_text(text):
            return {
                "ok": False,
                "status": 200,
                "contentType": normalized_content_type or "text/html",
                "finalUrl": resolved_url,
                "redirected": bool(final_url and normalize_job_url(final_url) != normalized_url),
                "extracted": {},
                "rawText": "",
                "applyUrl": "",
                "fetchedAt": now_iso(),
                "locationHint": "",
                "error": "Anti-bot interstitial page instead of job details.",
            }
        extracted_title = sanitize_job_title_candidate(fields.get("title") or "") or extract_fallback_job_title_from_html(
            html,
            resolved_url,
        )
        apply_url = extract_apply_url_from_html(html, resolved_url)
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
            "contentType": normalized_content_type or "text/html",
            "finalUrl": resolved_url,
            "redirected": bool(final_url and normalize_job_url(final_url) != normalized_url),
            "extracted": extracted,
            "rawText": text[:20000],
            "applyUrl": apply_url,
            "fetchedAt": now_iso(),
            "locationHint": location_hint,
        }
    except HTTPError as exc:
        return {
            "ok": False,
            "status": int(getattr(exc, "code", 0) or 0),
            "contentType": "",
            "finalUrl": normalized_url,
            "redirected": False,
            "extracted": {},
            "rawText": "",
            "applyUrl": "",
            "fetchedAt": now_iso(),
            "locationHint": "",
            "error": f"HTTP {int(getattr(exc, 'code', 0) or 0)}",
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
