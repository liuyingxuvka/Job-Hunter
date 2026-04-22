from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...ai.client import build_json_schema_request, build_text_input_messages, parse_response_json
from ...prompt_assets import load_prompt_asset
from ..analysis.service import ResponseRequestClient
from ..output.final_output import is_likely_parking_host, normalize_job_url
from .sources_helpers import dedupe_jobs_by_normalized_url, has_job_signal

_PAGE_JOB_ENUMERATION_SCHEMA: dict[str, Any] = {
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
                    "url": {"type": "string"},
                    "location": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["title", "url", "location", "summary"],
            },
        },
        "nextListingUrls": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["jobs", "nextListingUrls"],
}
_MAX_LINKS_FOR_ENUMERATION = 80
_MAX_PAGE_TEXT_CHARS = 3500
_MAX_NEW_JOBS_PER_ENUMERATION = 10


@dataclass(frozen=True)
class CareersPageEnumerationResult:
    jobs: list[dict[str, Any]]
    next_listing_urls: list[str]
    revisit_current_page: bool = False


def enumerate_jobs_from_careers_page(
    client: ResponseRequestClient | None,
    *,
    config: Mapping[str, Any] | None,
    company_name: str,
    page_url: str,
    page_text: str,
    links: list[Mapping[str, Any]],
    sample_job_urls: list[str] | None = None,
    seen_job_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    result = enumerate_jobs_and_listing_hints_from_careers_page(
        client,
        config=config,
        company_name=company_name,
        page_url=page_url,
        page_text=page_text,
        links=links,
        sample_job_urls=sample_job_urls,
        seen_job_urls=seen_job_urls,
    )
    return result.jobs


def enumerate_jobs_and_listing_hints_from_careers_page(
    client: ResponseRequestClient | None,
    *,
    config: Mapping[str, Any] | None,
    company_name: str,
    page_url: str,
    page_text: str,
    links: list[Mapping[str, Any]],
    sample_job_urls: list[str] | None = None,
    seen_job_urls: list[str] | None = None,
) -> CareersPageEnumerationResult:
    model = _job_enumeration_model(config)
    if client is None or not model:
        return CareersPageEnumerationResult(
            jobs=[],
            next_listing_urls=[],
        )
    allowed_links = _normalize_link_candidates(links[:_MAX_LINKS_FOR_ENUMERATION])
    request = build_json_schema_request(
        model=model,
        input_payload=build_text_input_messages(
            "You identify concrete official job detail pages for one company and return only structured JSON.",
            load_prompt_asset("search_ranking", "careers_page_job_enumeration_prompt.txt").format(
                company_name=str(company_name or "").strip(),
                page_url=str(page_url or "").strip(),
                page_text=_truncate_text(page_text, _MAX_PAGE_TEXT_CHARS),
                max_jobs=_MAX_NEW_JOBS_PER_ENUMERATION,
                sample_job_urls_json=json.dumps(_normalize_sample_job_urls(sample_job_urls), ensure_ascii=False),
                seen_job_urls_json=json.dumps(_normalize_sample_job_urls(seen_job_urls), ensure_ascii=False),
                links_json=json.dumps(allowed_links, ensure_ascii=False),
            ),
        ),
        schema_name="careers_page_job_enumeration",
        schema=_PAGE_JOB_ENUMERATION_SCHEMA,
        use_web_search=False,
    )
    response = client.create(request)
    payload = parse_response_json(response, f"Careers page job enumeration ({company_name})")
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return CareersPageEnumerationResult(
            jobs=[],
            next_listing_urls=[],
        )

    allowed_by_url = {
        normalize_job_url(item.get("url") or ""): dict(item)
        for item in allowed_links
        if normalize_job_url(item.get("url") or "")
    }
    seen_urls = set(_normalize_sample_job_urls(seen_job_urls))
    enumerated: list[dict[str, Any]] = []
    for raw_job in jobs:
        if not isinstance(raw_job, Mapping):
            continue
        normalized_url = normalize_job_url(raw_job.get("url") or "")
        if not normalized_url:
            continue
        if normalized_url in seen_urls:
            continue
        if is_likely_parking_host(normalized_url):
            continue
        link_info = allowed_by_url.get(normalized_url, {})
        title = str(raw_job.get("title") or link_info.get("text") or "").strip()
        if not title:
            continue
        summary = str(raw_job.get("summary") or "").strip()
        if not has_job_signal(title=title, url=normalized_url, summary=summary):
            continue
        enumerated.append(
            {
                "title": title,
                "location": str(raw_job.get("location") or "").strip(),
                "url": normalized_url,
                "datePosted": "",
                "summary": summary,
                "aiEnumerated": True,
            }
        )
    deduped_jobs = dedupe_jobs_by_normalized_url(enumerated)
    next_listing_urls = _normalize_next_listing_urls(
        payload.get("nextListingUrls"),
        current_page_url=page_url,
        allowed_links=allowed_by_url,
    )
    revisit_current_page = len(deduped_jobs) >= _MAX_NEW_JOBS_PER_ENUMERATION
    return CareersPageEnumerationResult(
        jobs=deduped_jobs[:_MAX_NEW_JOBS_PER_ENUMERATION],
        next_listing_urls=next_listing_urls,
        revisit_current_page=revisit_current_page,
    )


def _job_enumeration_model(config: Mapping[str, Any] | None) -> str:
    search = dict(config.get("search") or {}) if isinstance(config, Mapping) else {}
    model = str(search.get("model") or "").strip()
    if model:
        return model
    company_discovery = dict(config.get("companyDiscovery") or {}) if isinstance(config, Mapping) else {}
    return str(company_discovery.get("model") or "").strip()


def _normalize_link_candidates(links: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in links:
        if not isinstance(item, Mapping):
            continue
        url = normalize_job_url(item.get("url") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        normalized.append(
            {
                "text": str(item.get("text") or "").strip(),
                "url": url,
            }
        )
    return normalized


def _normalize_sample_job_urls(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values or []:
        url = normalize_job_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    return normalized[:4]


def _normalize_next_listing_urls(
    values: object,
    *,
    current_page_url: str,
    allowed_links: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    if not isinstance(values, list):
        return []
    current = normalize_job_url(current_page_url)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        url = normalize_job_url(item)
        if not url or url == current or url in seen:
            continue
        if is_likely_parking_host(url):
            continue
        if has_job_signal(title="", url=url, summary=""):
            continue
        link_info = allowed_links.get(url, {})
        title = str(link_info.get("text") or "").strip()
        if title and has_job_signal(title=title, url=url, summary=""):
            continue
        seen.add(url)
        normalized.append(url)
    return normalized[:20]


def _truncate_text(text: object, limit: int) -> str:
    raw = str(text or "").strip()
    if limit <= 0 or len(raw) <= limit:
        return raw
    return raw[:limit].rstrip() + "\n...[truncated]"


__all__ = [
    "CareersPageEnumerationResult",
    "enumerate_jobs_and_listing_hints_from_careers_page",
    "enumerate_jobs_from_careers_page",
]
