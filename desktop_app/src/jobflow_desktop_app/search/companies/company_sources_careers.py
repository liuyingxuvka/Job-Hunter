from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ...ai.client import build_json_schema_request, build_text_input_messages, parse_response_json
from ...prompt_assets import load_prompt_asset
from ..analysis.service import ResponseRequestClient
from ..output.final_output import normalize_job_url
from .ai_job_enumeration import enumerate_jobs_and_listing_hints_from_careers_page
from .company_sources_ats import fetch_supported_ats_jobs
from .selection import company_has_region_tag
from .state import company_has_materialized_jobs_entry
from .sources_fetchers import fetch_text, to_number
from .sources_helpers import (
    SUPPORTED_DIRECT_ATS_TYPES,
    collect_careers_page_job_candidates,
    collect_careers_page_link_snapshots,
    dedupe_jobs_by_normalized_url,
    detect_ats_from_url,
    extract_fallback_job_title_from_html,
    extract_location_from_text,
    has_job_signal,
    merge_unique_strings,
    strip_html_to_text,
)

_COMPANY_CAREERS_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "website": {"type": "string"},
        "jobsPageUrl": {"type": "string"},
        "pageType": {
            "type": "string",
            "enum": ["ats_board", "jobs_listing", "generic_careers", "not_found"],
        },
        "careersUrl": {"type": "string"},
        "sampleJobUrls": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["website", "jobsPageUrl", "pageType", "careersUrl", "sampleJobUrls"],
}
_JOB_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "location": {"type": "string"},
                    "url": {"type": "string"},
                    "summary": {"type": "string"},
                    "datePosted": {"type": "string"},
                    "availabilityHint": {"type": "string"},
                },
                "required": [
                    "title",
                    "company",
                    "location",
                    "url",
                    "summary",
                    "datePosted",
                    "availabilityHint",
                ],
            },
        }
    },
    "required": ["jobs"],
}
CAREERS_DISCOVERY_RETRY_LIMIT = 1
COMPANY_SEARCH_RETRY_LIMIT = 1
_SCRIPT_STYLE_BLOCK_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>",
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class CareersPageFetchTrace:
    jobs: list[dict[str, Any]]
    followed_links: list[str]
    source_path: str
    next_listing_urls: list[str]
    listing_page_cache_entry: dict[str, Any] | None = None


def discover_company_careers(
    client: ResponseRequestClient,
    *,
    config: Mapping[str, Any] | None,
    company_name: str,
) -> dict[str, Any]:
    normalized_name = str(company_name or "").strip()
    if not normalized_name:
        return _empty_company_careers_result()
    company_discovery = dict(config.get("companyDiscovery") or {}) if isinstance(config, Mapping) else {}
    model = str(company_discovery.get("model") or "").strip()
    if not model:
        return _empty_company_careers_result()
    request = build_json_schema_request(
        model=model,
        input_payload=build_text_input_messages(
            "You identify official company websites and official public jobs entry pages. Return only structured JSON.",
            (
                "Find the official website and the best official public jobs entry page for this company.\n"
                f"Company name: {normalized_name}\n\n"
                "Rules:\n"
                "- Use only the official company website, never aggregators or mirror pages.\n"
                "- If multiple official sites exist, prefer the global corporate site.\n"
                "- The jobs entry page should be the best public page from which real openings can be reached.\n"
                "- Prefer an ATS board or official jobs listing page over a generic culture/benefits careers landing page.\n"
                "- Use pageType=ats_board when the page is an ATS board or vendor-hosted jobs board.\n"
                "- Use pageType=jobs_listing when the page is an official listing/search page that exposes openings.\n"
                "- Use pageType=generic_careers only when you can find only a generic careers landing page.\n"
                "- Use pageType=not_found when you cannot identify a reliable official public jobs entry page.\n"
                "- Return 2 to 4 official public job detail page URLs in sampleJobUrls when you can identify them confidently.\n"
                "- sampleJobUrls must be concrete official job detail pages for this same company, never aggregators, team pages, filters, or saved-jobs pages.\n"
                "- If you cannot identify reliable official detail pages, return an empty sampleJobUrls array.\n"
                "- If no jobs entry page is found, return empty strings for jobsPageUrl and careersUrl.\n"
                "- Set careersUrl equal to jobsPageUrl for compatibility.\n"
                "Output only JSON matching the schema."
            ),
        ),
        schema_name="company_careers_discovery",
        schema=_COMPANY_CAREERS_DISCOVERY_SCHEMA,
        use_web_search=True,
    )
    response = _create_with_retry(
        client,
        request,
        retry_limit=CAREERS_DISCOVERY_RETRY_LIMIT,
    )
    payload = parse_response_json(response, f"Company careers discovery ({normalized_name})")
    jobs_page_url = str(payload.get("jobsPageUrl") or payload.get("careersUrl") or "").strip()
    page_type = _normalize_jobs_page_type(payload.get("pageType"))
    sample_job_urls = _normalize_sample_job_urls(payload.get("sampleJobUrls"))
    if jobs_page_url and page_type == "not_found":
        page_type = _infer_jobs_page_type_from_url(jobs_page_url)
    if not jobs_page_url:
        page_type = "not_found"
    return {
        "website": str(payload.get("website") or "").strip(),
        "jobsPageUrl": jobs_page_url,
        "pageType": page_type,
        "careersUrl": jobs_page_url,
        "sampleJobUrls": sample_job_urls,
    }

def resolve_company_jobs_entries(
    client: ResponseRequestClient | None,
    *,
    config: Mapping[str, Any] | None,
    companies: list[Mapping[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    normalized_companies = [dict(company) for company in companies if isinstance(company, Mapping)]
    if client is None or limit <= 0:
        return normalized_companies
    company_discovery = dict(config.get("companyDiscovery") or {}) if isinstance(config, Mapping) else {}
    model = str(company_discovery.get("model") or "").strip()
    if not model:
        return normalized_companies

    candidates = sorted(
        normalized_companies,
        key=lambda company: (
            -float(company.get("aiCompanyFitScore") or 0.0),
            -int(to_number(company.get("signalCount"), 0)),
            -int(to_number(company.get("repeatCount"), 0)),
            str(company.get("name") or "").casefold(),
        ),
    )
    processed = 0
    for company in candidates:
        if processed >= limit:
            break
        if company_has_materialized_jobs_entry(company):
            continue
        if str(company.get("atsType") or "").strip() and str(company.get("atsId") or "").strip():
            continue
        company_name = str(company.get("name") or "").strip()
        if not company_name:
            continue
        cache = normalize_company_careers_discovery_cache(company.get("careersDiscoveryCache"))
        if not cache:
            try:
                discovered = discover_company_careers(
                    client,
                    config=config,
                    company_name=company_name,
                )
            except Exception:
                processed += 1
                continue
            cache = normalize_company_careers_discovery_cache(discovered)
            if cache:
                company["careersDiscoveryCache"] = cache
        if cache.get("website") and not str(company.get("website") or "").strip():
            company["website"] = str(cache.get("website") or "").strip()
        jobs_page_url = str(cache.get("jobsPageUrl") or "").strip()
        if jobs_page_url:
            company["jobsPageUrl"] = jobs_page_url
            company["careersUrl"] = jobs_page_url
        page_type = str(cache.get("pageType") or "").strip().lower()
        if page_type:
            company["jobsPageType"] = page_type
        if cache.get("sampleJobUrls"):
            company["sampleJobUrls"] = merge_unique_strings(
                company.get("sampleJobUrls"),
                cache.get("sampleJobUrls"),
            )
        processed += 1
    return normalized_companies


def normalize_company_careers_discovery_cache(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    source = dict(raw)
    if not any(
        key in source
        for key in ("website", "jobsPageUrl", "pageType", "careersUrl", "sampleJobUrls")
    ):
        return {}
    jobs_page_url = str(source.get("jobsPageUrl") or source.get("careersUrl") or "").strip()
    page_type = _normalize_jobs_page_type(source.get("pageType"))
    if jobs_page_url and page_type == "not_found":
        page_type = _infer_jobs_page_type_from_url(jobs_page_url)
    return {
        "website": str(source.get("website") or "").strip(),
        "jobsPageUrl": jobs_page_url,
        "pageType": page_type,
        "careersUrl": jobs_page_url,
        "sampleJobUrls": _normalize_sample_job_urls(source.get("sampleJobUrls")),
    }


def build_company_search_cache_key(
    *,
    company_name: str,
    company_website: str = "",
    jobs_page_url: str = "",
    page_type: str = "",
    sample_job_urls: list[str] | None = None,
    known_job_urls: list[str] | None = None,
    query: str,
) -> str:
    payload = {
        "companyName": str(company_name or "").strip().casefold(),
        "companyWebsite": normalize_job_url(company_website),
        "jobsPageUrl": normalize_job_url(jobs_page_url),
        "pageType": _normalize_jobs_page_type(page_type),
        "sampleJobUrls": sorted(_normalize_sample_job_urls(sample_job_urls)),
        "knownJobUrls": sorted(_normalize_known_job_urls(known_job_urls)),
        "query": str(query or "").strip().casefold(),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(serialized.encode("utf-8", errors="ignore")).hexdigest()


def openai_search_jobs(
    client: ResponseRequestClient,
    *,
    config: Mapping[str, Any] | None,
    company_name: str,
    company_website: str = "",
    jobs_page_url: str = "",
    page_type: str = "",
    sample_job_urls: list[str] | None = None,
    known_job_urls: list[str] | None = None,
    query: str,
) -> list[dict[str, str]]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return []
    search = dict(config.get("search") or {}) if isinstance(config, Mapping) else {}
    model = str(search.get("model") or "").strip()
    if not model:
        return []
    max_jobs_per_query = max(1, int(to_number(search.get("maxJobsPerQuery"), 20)))
    request = build_json_schema_request(
        model=model,
        input_payload=build_text_input_messages(
            "You identify concrete public job detail pages for one company and return only structured JSON.",
            load_prompt_asset("search_ranking", "company_job_search_prompt.txt").format(
                company_name=str(company_name or "").strip(),
                company_website=str(company_website or "").strip() or "N/A",
                jobs_page_url=str(jobs_page_url or "").strip() or "N/A",
                page_type=str(page_type or "").strip() or "unknown",
                sample_job_urls_json=json.dumps(_normalize_sample_job_urls(sample_job_urls), ensure_ascii=False),
                known_job_urls_json=json.dumps(_normalize_known_job_urls(known_job_urls), ensure_ascii=False),
                query=normalized_query,
                max_jobs=max_jobs_per_query,
            ),
        ),
        schema_name="job_search_results",
        schema=_JOB_SEARCH_SCHEMA,
        use_web_search=True,
    )
    response = _create_with_retry(
        client,
        request,
        retry_limit=COMPANY_SEARCH_RETRY_LIMIT,
    )
    payload = parse_response_json(response, f"Job search ({normalized_query})")
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return []
    filtered: list[dict[str, str]] = []
    for raw_job in jobs:
        if not isinstance(raw_job, Mapping):
            continue
        if not has_job_signal(
            title=raw_job.get("title") or "",
            url=raw_job.get("url") or "",
            summary=raw_job.get("summary") or "",
        ):
            continue
        filtered.append(
            {
                "title": str(raw_job.get("title") or "").strip(),
                "company": str(raw_job.get("company") or "").strip(),
                "location": str(raw_job.get("location") or "").strip(),
                "url": str(raw_job.get("url") or "").strip(),
                "summary": str(raw_job.get("summary") or "").strip(),
                "datePosted": str(raw_job.get("datePosted") or "").strip(),
                "availabilityHint": str(raw_job.get("availabilityHint") or "").strip(),
            }
        )
    return filtered


def fetch_careers_page_jobs_with_trace(
    url: str,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
    sample_job_urls: list[str] | None = None,
    seen_job_urls: list[str] | None = None,
    client: ResponseRequestClient | None = None,
    company_name: str = "",
    listing_page_cache_entry: Mapping[str, Any] | None = None,
) -> CareersPageFetchTrace:
    normalized_url = str(url or "").strip()
    if not normalized_url:
        return CareersPageFetchTrace(
            jobs=[],
            followed_links=[],
            source_path="company_page",
            next_listing_urls=[],
        )
    direct_ats = detect_ats_from_url(normalized_url)
    if direct_ats and direct_ats.get("type") in SUPPORTED_DIRECT_ATS_TYPES:
        jobs = fetch_supported_ats_jobs(
            str(direct_ats.get("type") or ""),
            str(direct_ats.get("id") or ""),
            config=config,
            timeout_seconds=timeout_seconds,
        )
        return CareersPageFetchTrace(
            jobs=jobs,
            followed_links=[normalized_url],
            source_path="ats_board",
            next_listing_urls=[],
        )
    try:
        html, final_url = fetch_text(
            normalized_url,
            config=config,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        sample_jobs = _fetch_sample_job_candidates(
            sample_job_urls,
            config=config,
            timeout_seconds=timeout_seconds,
        )
        if sample_jobs:
            return CareersPageFetchTrace(
                jobs=sample_jobs,
                followed_links=[],
                source_path="sample_job_urls",
                next_listing_urls=[],
            )
        raise
    resolved_url = final_url or normalized_url
    page_fingerprint = _build_listing_page_fingerprint(html)
    cached_entry = _normalize_listing_page_cache_entry(listing_page_cache_entry)
    if cached_entry.get("pageFingerprint") == page_fingerprint:
        cached_jobs = dedupe_jobs_by_normalized_url(
            [dict(item) for item in cached_entry.get("jobs", []) if isinstance(item, Mapping)]
        )
        return CareersPageFetchTrace(
            jobs=cached_jobs,
            followed_links=[],
            source_path="company_page_ai_cache" if cached_jobs else "company_page_cache",
            next_listing_urls=merge_unique_strings(cached_entry.get("nextListingUrls")),
            listing_page_cache_entry={
                "pageFingerprint": page_fingerprint,
                "jobs": cached_jobs,
                "nextListingUrls": merge_unique_strings(cached_entry.get("nextListingUrls")),
            },
        )
    jobs = collect_careers_page_job_candidates(
        html,
        resolved_url,
        sample_job_urls=sample_job_urls,
    )
    try:
        enumeration = enumerate_jobs_and_listing_hints_from_careers_page(
            client,
            config=config,
            company_name=company_name,
            page_url=resolved_url,
            page_text=_clean_careers_page_text_for_ai(html),
            links=collect_careers_page_link_snapshots(html, resolved_url),
            sample_job_urls=sample_job_urls,
            seen_job_urls=seen_job_urls,
        )
    except Exception:
        fallback_jobs = dedupe_jobs_by_normalized_url(
            [
                *jobs,
                *_fetch_sample_job_candidates(
                    sample_job_urls,
                    config=config,
                    timeout_seconds=timeout_seconds,
                ),
            ]
        )
        if fallback_jobs:
            return CareersPageFetchTrace(
                jobs=fallback_jobs,
                followed_links=[],
                source_path="sample_job_urls" if not jobs else "company_page",
                next_listing_urls=[],
                listing_page_cache_entry={
                    "pageFingerprint": page_fingerprint,
                    "jobs": fallback_jobs,
                    "nextListingUrls": [],
                },
            )
        raise
    merged_jobs = dedupe_jobs_by_normalized_url([*jobs, *enumeration.jobs])
    next_listing_urls = merge_unique_strings(
        enumeration.next_listing_urls,
        [resolved_url] if getattr(enumeration, "revisit_current_page", False) else [],
    )
    if merged_jobs:
        return CareersPageFetchTrace(
            jobs=merged_jobs,
            followed_links=[],
            source_path="company_page_ai" if enumeration.jobs else "company_page",
            next_listing_urls=next_listing_urls,
            listing_page_cache_entry={
                "pageFingerprint": page_fingerprint,
                "jobs": merged_jobs,
                "nextListingUrls": next_listing_urls,
            },
        )
    sample_jobs = _fetch_sample_job_candidates(
        sample_job_urls,
        config=config,
        timeout_seconds=timeout_seconds,
    )
    if sample_jobs:
        return CareersPageFetchTrace(
            jobs=sample_jobs,
            followed_links=[],
            source_path="sample_job_urls",
            next_listing_urls=next_listing_urls,
            listing_page_cache_entry={
                "pageFingerprint": page_fingerprint,
                "jobs": sample_jobs,
                "nextListingUrls": next_listing_urls,
            },
        )
    return CareersPageFetchTrace(
        jobs=[],
        followed_links=[],
        source_path="company_page",
        next_listing_urls=next_listing_urls,
        listing_page_cache_entry={
            "pageFingerprint": page_fingerprint,
            "jobs": [],
            "nextListingUrls": next_listing_urls,
        },
    )


def _clean_careers_page_text_for_ai(html: object) -> str:
    raw_html = str(html or "")
    html_without_script_style = _SCRIPT_STYLE_BLOCK_RE.sub(" ", raw_html)
    return strip_html_to_text(html_without_script_style)


def _fetch_sample_job_candidates(
    sample_job_urls: list[str] | None,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> list[dict[str, Any]]:
    if not isinstance(sample_job_urls, list):
        return []
    jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for raw_url in sample_job_urls[:4]:
        normalized_url = normalize_job_url(raw_url)
        if not normalized_url or normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        try:
            html, final_url = fetch_text(
                normalized_url,
                config=config,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            continue
        resolved_url = normalize_job_url(final_url or normalized_url)
        raw_text = strip_html_to_text(html)
        title = extract_fallback_job_title_from_html(html, resolved_url)
        summary = raw_text[:400].strip()
        if not resolved_url or not title:
            continue
        jobs.append(
            {
                "title": title,
                "location": extract_location_from_text(raw_text),
                "url": resolved_url,
                "datePosted": "",
                "summary": summary,
            }
        )
    return dedupe_jobs_by_normalized_url(jobs)


def _build_listing_page_fingerprint(html: object) -> str:
    normalized = strip_html_to_text(html)
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()


def _normalize_listing_page_cache_entry(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(raw or {}) if isinstance(raw, Mapping) else {}
    return {
        "pageFingerprint": str(source.get("pageFingerprint") or "").strip(),
        "jobs": [
            dict(item)
            for item in source.get("jobs", [])
            if isinstance(item, Mapping)
        ],
        "nextListingUrls": [
            str(item).strip()
            for item in source.get("nextListingUrls", [])
            if str(item).strip()
        ],
    }


def fetch_careers_page_jobs(
    url: str,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
    sample_job_urls: list[str] | None = None,
    client: ResponseRequestClient | None = None,
    company_name: str = "",
) -> list[dict[str, Any]]:
    return fetch_careers_page_jobs_with_trace(
        url,
        config=config,
        timeout_seconds=timeout_seconds,
        sample_job_urls=sample_job_urls,
        client=client,
        company_name=company_name,
    ).jobs


def company_search_fallback_enabled(company: Mapping[str, Any], config: Mapping[str, Any] | None) -> bool:
    sources = dict(config.get("sources") or {}) if isinstance(config, Mapping) else {}
    if sources.get("enableCompanySearchFallback") is False:
        return False
    fallback_regions = [
        str(item or "").strip()
        for item in sources.get("fallbackSearchRegions", [])
        if str(item or "").strip()
    ]
    if not fallback_regions:
        return True
    return any(company_has_region_tag(company, region_tag) for region_tag in fallback_regions)


def build_company_search_fallback_query(
    company: Mapping[str, Any],
    config: Mapping[str, Any] | None,
) -> str:
    del config
    name = str(company.get("name") or "").strip()
    if not name:
        return ""
    search_host = _company_search_host(company)
    query_parts: list[str] = []
    if search_host:
        query_parts.append(f"site:{search_host}")
    query_parts.extend([name, "careers", "jobs"])
    query = " ".join(part.strip() for part in query_parts if str(part or "").strip())
    return query[:180].strip()


def _company_search_host(company: Mapping[str, Any]) -> str:
    for raw_value in (
        company.get("jobsPageUrl"),
        company.get("careersUrl"),
        company.get("website"),
    ):
        try:
            host = str(urlsplit(str(raw_value or "").strip()).hostname or "").strip().casefold()
        except Exception:
            host = ""
        if host:
            return host[4:] if host.startswith("www.") else host
    return ""


def _create_with_retry(
    client: ResponseRequestClient,
    request: Mapping[str, Any],
    *,
    retry_limit: int,
) -> dict[str, Any]:
    attempts = max(1, int(retry_limit) + 1)
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return client.create(request)
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error

def _empty_company_careers_result() -> dict[str, Any]:
    return {
        "website": "",
        "jobsPageUrl": "",
        "pageType": "not_found",
        "careersUrl": "",
        "sampleJobUrls": [],
    }


def _normalize_sample_job_urls(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        url = normalize_job_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    return normalized[:4]


def _normalize_known_job_urls(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        url = normalize_job_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    return normalized[:40]


def _normalize_jobs_page_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"ats_board", "jobs_listing", "generic_careers", "not_found"}:
        return normalized
    return "not_found"


def _infer_jobs_page_type_from_url(url: object) -> str:
    detected = detect_ats_from_url(url)
    if detected and str(detected.get("type") or "").strip().lower() in SUPPORTED_DIRECT_ATS_TYPES:
        return "ats_board"
    text = str(url or "").strip().lower()
    if text:
        return "jobs_listing"
    return "not_found"


__all__ = [
    "build_company_search_cache_key",
    "build_company_search_fallback_query",
    "company_search_fallback_enabled",
    "discover_company_careers",
    "fetch_careers_page_jobs",
    "fetch_careers_page_jobs_with_trace",
    "normalize_company_careers_discovery_cache",
    "openai_search_jobs",
]
