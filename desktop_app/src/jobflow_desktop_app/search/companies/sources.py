from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from ..analysis.service import ResponseRequestClient
from .company_sources_ats import (
    fetch_greenhouse_jobs,
    fetch_lever_jobs,
    fetch_smartrecruiters_jobs,
    fetch_supported_ats_jobs,
    partition_supported_companies,
    resolve_supported_company_ats,
)
from .company_sources_careers import (
    build_company_search_fallback_query,
    company_search_fallback_enabled,
    discover_careers_from_website,
    discover_company_careers,
    fetch_careers_page_jobs,
    openai_search_jobs,
)
from .company_sources_enrichment import (
    build_found_job_records,
    merge_company_source_jobs,
)
from .discovery import merge_unique_strings
from .sources_fetchers import remaining_seconds as _remaining_seconds
from .sources_fetchers import to_number as _to_number
from .sources_helpers import (
    _COMMON_CAREERS_PATHS,
    JOB_LINK_HARD_CAP_PER_COMPANY,
    SUPPORTED_DIRECT_ATS_TYPES,
    collect_careers_page_job_candidates,
    dedupe_jobs_by_normalized_url,
    detect_ats_from_url,
    get_normalized_company_job_url_list,
    is_stale,
    normalize_company_job,
    normalize_company_job_coverage_state,
    select_company_jobs_for_coverage,
    strip_html_to_text,
)


@dataclass(frozen=True)
class CompanySourcesFetchResult:
    jobs: list[dict[str, Any]]
    processed_companies: list[dict[str, Any]]
    remaining_companies: list[dict[str, Any]]
    jobs_found_count: int
    companies_handled_count: int


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def collect_supported_company_source_jobs(
    companies: list[Mapping[str, Any]],
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None = None,
    progress_callback: Callable[[str], None] | None = None,
    client: ResponseRequestClient | None = None,
) -> CompanySourcesFetchResult:
    deadline = time.monotonic() + max(1, int(timeout_seconds)) if timeout_seconds else None
    selected_companies = [dict(item) for item in companies if isinstance(item, Mapping)]
    supported_companies, remaining_companies = partition_supported_companies(selected_companies)

    sources = dict(config.get("sources") or {}) if isinstance(config, Mapping) else {}
    filters = dict(config.get("filters") or {}) if isinstance(config, Mapping) else {}
    max_jobs_per_company = max(1, int(_to_number(sources.get("maxJobsPerCompany"), JOB_LINK_HARD_CAP_PER_COMPANY)))
    max_post_age_days = max(0, int(_to_number(filters.get("maxPostAgeDays"), 0)))
    fetched_jobs: list[dict[str, Any]] = []
    processed_companies: list[dict[str, Any]] = []
    discovered_now = now_iso()
    can_use_web_search = client is not None and sources.get("enableCompanySearchFallback") is not False
    analysis = dict(config.get("analysis") or {}) if isinstance(config, Mapping) else {}
    detail_fetch_cap = max(0, int(_to_number(analysis.get("jdFetchMaxJobsPerRun"), 0)))
    detail_fetch_count = 0
    generic_candidates: list[tuple[dict[str, Any], bool]] = [
        (dict(item), False) for item in remaining_companies if isinstance(item, Mapping)
    ]

    for index, company in enumerate(supported_companies, start=1):
        remaining_seconds = _remaining_seconds(deadline)
        if deadline is not None and remaining_seconds <= 0:
            raise TimeoutError("Python company sources stage timed out while fetching ATS jobs.")
        company_name = str(company.get("name") or "").strip() or "Unknown company"
        ats_type = str(company.get("atsType") or "").strip().lower()
        ats_id = str(company.get("atsId") or "").strip()
        if progress_callback is not None:
            progress_callback(
                f"Python company sources {index}/{len(supported_companies)}: {company_name} | {ats_type}"
            )
        try:
            raw_jobs = fetch_supported_ats_jobs(
                ats_type,
                ats_id,
                config=config,
                timeout_seconds=remaining_seconds,
            )
        except Exception:
            company["snapshotComplete"] = False
            company.pop("cooldownUntil", None)
            generic_candidates.append((company, True))
            continue

        company["snapshotComplete"] = False
        company.pop("cooldownUntil", None)
        tags = merge_unique_strings(company.get("tags"))
        normalized_jobs = [
            normalize_company_job(
                job,
                company_name=company_name,
                ats_type=ats_type,
                company_tags=tags,
                config=config,
                discovered_at=discovered_now,
            )
            for job in raw_jobs
        ]
        filtered_jobs = [
            job
            for job in normalized_jobs
            if job.get("url") and not is_stale(job.get("datePosted") or "", max_post_age_days)
        ]
        snapshot_jobs = dedupe_jobs_by_normalized_url(filtered_jobs)
        known_job_urls = set(get_normalized_company_job_url_list(company, "knownJobUrls"))
        snapshot_new_jobs_count = sum(1 for job in snapshot_jobs if job["url"] not in known_job_urls)
        coverage_selection = select_company_jobs_for_coverage(
            company=company,
            jobs=snapshot_jobs,
            limit=max_jobs_per_company,
        )
        selected_jobs, detail_fetch_count = enrich_selected_jobs_with_details(
            coverage_selection["jobs"],
            config=config,
            timeout_seconds=_remaining_seconds(deadline),
            detail_fetch_cap=detail_fetch_cap,
            already_fetched_count=detail_fetch_count,
            progress_callback=progress_callback,
        )
        if coverage_selection["changed"]:
            if coverage_selection["jobLinkCoverage"]:
                company["jobLinkCoverage"] = coverage_selection["jobLinkCoverage"]
            else:
                company.pop("jobLinkCoverage", None)
        company["lastSearchedAt"] = discovered_now
        company["lastJobsFoundCount"] = len(snapshot_jobs)
        company["lastNewJobsCount"] = snapshot_new_jobs_count
        company["snapshotComplete"] = True
        company["snapshotJobUrls"] = [job["url"] for job in snapshot_jobs]
        company["knownJobUrls"] = merge_unique_strings(
            get_normalized_company_job_url_list(company, "knownJobUrls"),
            company["snapshotJobUrls"],
        )
        company.pop("cooldownUntil", None)
        processed_companies.append(company)
        fetched_jobs.extend(selected_jobs)

    generic_remaining: list[dict[str, Any]] = []
    for index, (company, inherited_transient_error) in enumerate(generic_candidates, start=1):
        remaining_seconds = _remaining_seconds(deadline)
        if deadline is not None and remaining_seconds <= 0:
            raise TimeoutError("Python company sources stage timed out while fetching careers pages.")
        company_name = str(company.get("name") or "").strip() or "Unknown company"
        careers_url = str(company.get("careersUrl") or "").strip()
        website = str(company.get("website") or "").strip()
        had_transient_error = inherited_transient_error
        company["snapshotComplete"] = False
        company.pop("cooldownUntil", None)
        if not careers_url and website:
            try:
                careers_url = discover_careers_from_website(
                    website,
                    config=config,
                    timeout_seconds=remaining_seconds,
                )
                if careers_url:
                    company["careersUrl"] = careers_url
            except Exception:
                had_transient_error = True
        if (not website or not careers_url) and can_use_web_search and company_name:
            try:
                discovered = discover_company_careers(
                    client,
                    config=config,
                    company_name=company_name,
                )
                if discovered.get("website") and not website:
                    company["website"] = str(discovered.get("website") or "").strip()
                    website = str(company.get("website") or "").strip()
                if discovered.get("careersUrl") and not careers_url:
                    company["careersUrl"] = str(discovered.get("careersUrl") or "").strip()
                    careers_url = str(company.get("careersUrl") or "").strip()
            except Exception:
                had_transient_error = True
        raw_jobs: list[dict[str, Any]] = []
        if careers_url:
            if progress_callback is not None:
                progress_callback(
                    f"Python company pages {index}/{len(generic_candidates)}: {company_name} | careers_page"
                )
            try:
                raw_jobs = fetch_careers_page_jobs(
                    careers_url,
                    config=config,
                    timeout_seconds=remaining_seconds,
                )
            except Exception:
                had_transient_error = True
                raw_jobs = []
        if not raw_jobs and can_use_web_search and company_search_fallback_enabled(company, config):
            fallback_query = build_company_search_fallback_query(company, config)
            if fallback_query:
                try:
                    if progress_callback is not None:
                        progress_callback(f"Python company fallback: {company_name} | {fallback_query}")
                    fallback_jobs = openai_search_jobs(
                        client,
                        config=config,
                        query=fallback_query,
                    )
                    raw_jobs = [
                        {
                            "title": str(job.get("title") or "").strip(),
                            "company": str(job.get("company") or company_name).strip(),
                            "location": str(job.get("location") or "").strip(),
                            "url": str(job.get("url") or "").strip(),
                            "datePosted": str(job.get("datePosted") or "").strip(),
                            "summary": str(job.get("summary") or "").strip(),
                            "source": f"company_search:{company_name}",
                            "sourceType": "company_search",
                        }
                        for job in fallback_jobs
                        if isinstance(job, Mapping) and str(job.get("url") or "").strip()
                    ]
                except Exception:
                    had_transient_error = True
        if not raw_jobs:
            company["lastSearchedAt"] = discovered_now
            company["lastJobsFoundCount"] = 0
            company["lastNewJobsCount"] = 0
            company.pop("snapshotJobUrls", None)
            if had_transient_error:
                generic_remaining.append(company)
            else:
                company["snapshotComplete"] = True
                processed_companies.append(company)
            continue
        tags = merge_unique_strings(company.get("tags"))
        normalized_jobs = [
            normalize_company_job(
                job,
                company_name=company_name,
                ats_type="careers_page",
                company_tags=tags,
                config=config,
                discovered_at=discovered_now,
            )
            for job in raw_jobs
        ]
        filtered_jobs = [
            job
            for job in normalized_jobs
            if job.get("url") and not is_stale(job.get("datePosted") or "", max_post_age_days)
        ]
        snapshot_jobs = dedupe_jobs_by_normalized_url(filtered_jobs)
        if not snapshot_jobs:
            generic_remaining.append(company)
            continue
        company["snapshotComplete"] = False
        company.pop("cooldownUntil", None)
        known_job_urls = set(get_normalized_company_job_url_list(company, "knownJobUrls"))
        snapshot_new_jobs_count = sum(1 for job in snapshot_jobs if job["url"] not in known_job_urls)
        coverage_selection = select_company_jobs_for_coverage(
            company=company,
            jobs=snapshot_jobs,
            limit=max_jobs_per_company,
        )
        selected_jobs, detail_fetch_count = enrich_selected_jobs_with_details(
            coverage_selection["jobs"],
            config=config,
            timeout_seconds=_remaining_seconds(deadline),
            detail_fetch_cap=detail_fetch_cap,
            already_fetched_count=detail_fetch_count,
            progress_callback=progress_callback,
        )
        if coverage_selection["changed"]:
            if coverage_selection["jobLinkCoverage"]:
                company["jobLinkCoverage"] = coverage_selection["jobLinkCoverage"]
            else:
                company.pop("jobLinkCoverage", None)
        company["lastSearchedAt"] = discovered_now
        company["lastJobsFoundCount"] = len(snapshot_jobs)
        company["lastNewJobsCount"] = snapshot_new_jobs_count
        company["snapshotComplete"] = True
        company["snapshotJobUrls"] = [job["url"] for job in snapshot_jobs]
        company["knownJobUrls"] = merge_unique_strings(
            get_normalized_company_job_url_list(company, "knownJobUrls"),
            company["snapshotJobUrls"],
        )
        company.pop("cooldownUntil", None)
        processed_companies.append(company)
        fetched_jobs.extend(selected_jobs)

    final_jobs = merge_company_source_jobs([], fetched_jobs)
    return CompanySourcesFetchResult(
        jobs=final_jobs,
        processed_companies=processed_companies,
        remaining_companies=generic_remaining,
        jobs_found_count=len(final_jobs),
        companies_handled_count=len(processed_companies),
    )


def fetch_job_details(
    url: str,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> dict[str, Any]:
    from .company_sources_enrichment import fetch_job_details as _fetch_job_details

    return _fetch_job_details(
        url,
        config=config,
        timeout_seconds=timeout_seconds,
    )


def enrich_job_with_details(
    job: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> dict[str, Any]:
    from .company_sources_enrichment import enrich_job_with_details as _enrich_job_with_details

    return _enrich_job_with_details(
        job,
        config=config,
        timeout_seconds=timeout_seconds,
        details_fetcher=fetch_job_details,
    )


def enrich_selected_jobs_with_details(
    jobs: list[Mapping[str, Any]],
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
    detail_fetch_cap: int,
    already_fetched_count: int,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    from .company_sources_enrichment import enrich_selected_jobs_with_details as _enrich_selected_jobs_with_details

    return _enrich_selected_jobs_with_details(
        jobs,
        config=config,
        timeout_seconds=timeout_seconds,
        detail_fetch_cap=detail_fetch_cap,
        already_fetched_count=already_fetched_count,
        progress_callback=progress_callback,
        detail_enricher=enrich_job_with_details,
    )


__all__ = [
    "CompanySourcesFetchResult",
    "JOB_LINK_HARD_CAP_PER_COMPANY",
    "SUPPORTED_DIRECT_ATS_TYPES",
    "_COMMON_CAREERS_PATHS",
    "build_found_job_records",
    "build_company_search_fallback_query",
    "collect_careers_page_job_candidates",
    "collect_supported_company_source_jobs",
    "company_search_fallback_enabled",
    "dedupe_jobs_by_normalized_url",
    "detect_ats_from_url",
    "discover_careers_from_website",
    "discover_company_careers",
    "enrich_job_with_details",
    "fetch_careers_page_jobs",
    "fetch_greenhouse_jobs",
    "fetch_job_details",
    "fetch_lever_jobs",
    "fetch_smartrecruiters_jobs",
    "fetch_supported_ats_jobs",
    "get_normalized_company_job_url_list",
    "merge_company_source_jobs",
    "normalize_company_job",
    "normalize_company_job_coverage_state",
    "openai_search_jobs",
    "partition_supported_companies",
    "resolve_supported_company_ats",
    "select_company_jobs_for_coverage",
    "strip_html_to_text",
]
