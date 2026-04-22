from __future__ import annotations

import time
from collections.abc import Mapping
from contextlib import contextmanager
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
    build_company_search_cache_key,
    build_company_search_fallback_query,
    company_search_fallback_enabled,
    discover_company_careers,
    fetch_careers_page_jobs_with_trace,
    normalize_company_careers_discovery_cache,
    openai_search_jobs,
)
from .ai_ranking import prerank_company_jobs_for_candidate
from .company_sources_enrichment import (
    build_found_job_records,
    merge_company_source_jobs,
)
from .discovery import merge_unique_strings
from .state import company_source_coverage_complete
from ..run_state import analysis_completed
from ..output.final_output import normalize_job_url
from .sources_fetchers import remaining_seconds as _remaining_seconds
from .sources_fetchers import to_number as _to_number
from .sources_helpers import (
    _COMMON_CAREERS_PATHS,
    JOB_LINK_HARD_CAP_PER_COMPANY,
    NON_ATS_LISTING_PAGES_PER_RUN,
    SUPPORTED_DIRECT_ATS_TYPES,
    collect_careers_page_job_candidates,
    dedupe_jobs_by_normalized_url,
    detect_ats_from_url,
    get_normalized_company_job_url_list,
    is_stale,
    is_likely_noise_title,
    normalize_company_job,
    normalize_company_job_coverage_state,
    normalize_job_page_coverage_state,
    overlay_cached_job_prerank_scores,
    select_company_jobs_for_coverage,
    select_listing_urls_for_processing,
    strip_html_to_text,
    update_job_page_coverage_state,
)
from ..state.work_unit_state import clear_work_unit_state, record_technical_failure

COMPANY_CAREERS_DISCOVERY_TIMEOUT_SECONDS = 120
COMPANY_CAREERS_PAGE_FETCH_TIMEOUT_SECONDS = 15
COMPANY_SEARCH_FALLBACK_TIMEOUT_SECONDS = 180
COMPANY_JOB_PRERANK_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class CompanySourcesFetchResult:
    jobs: list[dict[str, Any]]
    processed_companies: list[dict[str, Any]]
    remaining_companies: list[dict[str, Any]]
    jobs_found_count: int
    companies_handled_count: int


@dataclass(frozen=True)
class _SourceStageBudget:
    deadline: float | None

    @classmethod
    def from_timeout_seconds(cls, timeout_seconds: int | None) -> "_SourceStageBudget":
        deadline = time.monotonic() + max(1, int(timeout_seconds)) if timeout_seconds else None
        return cls(deadline=deadline)

    def exhausted(self) -> bool:
        remaining_seconds = _remaining_seconds(self.deadline)
        return remaining_seconds is not None and remaining_seconds <= 0

    def remaining_timeout(self) -> int | None:
        remaining_seconds = _remaining_seconds(self.deadline)
        if remaining_seconds is None:
            return None
        return max(1, int(remaining_seconds))

    def capped_timeout(self, cap_seconds: int) -> int | None:
        remaining_seconds = _remaining_seconds(self.deadline)
        if remaining_seconds is None:
            return max(1, int(cap_seconds))
        return max(1, min(int(remaining_seconds), int(cap_seconds)))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def _temporary_client_timeout(
    client: ResponseRequestClient | None,
    *,
    timeout_seconds: int | None,
):
    if client is None or timeout_seconds is None:
        yield
        return
    marker = object()
    original_timeout = getattr(client, "timeout_seconds", marker)
    if original_timeout is marker:
        yield
        return
    try:
        client.timeout_seconds = max(1, int(timeout_seconds))
        yield
    finally:
        client.timeout_seconds = original_timeout


def _job_explicitly_unavailable(job: Mapping[str, Any]) -> bool:
    availability_hint = str(job.get("availabilityHint") or "").strip().casefold()
    if not availability_hint:
        return False
    return any(
        token in availability_hint
        for token in ("closed", "expired", "archived", "filled", "inactive", "unavailable")
    )


def _prepare_snapshot_jobs(
    jobs: list[dict[str, Any]],
    *,
    max_post_age_days: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    diagnostics = {
        "rawJobsFetched": len(jobs),
        "filteredMissingUrl": 0,
        "filteredMissingTitle": 0,
        "filteredNoiseTitle": 0,
        "filteredUnavailable": 0,
        "filteredStale": 0,
        "filteredDuplicate": 0,
    }
    snapshot_jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for job in jobs:
        url = str(job.get("url") or "").strip()
        if not url:
            diagnostics["filteredMissingUrl"] += 1
            continue
        title = str(job.get("title") or "").strip()
        if not title:
            diagnostics["filteredMissingTitle"] += 1
            continue
        if is_likely_noise_title(title) and not bool(job.get("aiEnumerated")):
            diagnostics["filteredNoiseTitle"] += 1
            continue
        if _job_explicitly_unavailable(job):
            diagnostics["filteredUnavailable"] += 1
            continue
        if is_stale(job.get("datePosted") or "", max_post_age_days):
            diagnostics["filteredStale"] += 1
            continue
        if url in seen_urls:
            diagnostics["filteredDuplicate"] += 1
            continue
        seen_urls.add(url)
        snapshot_jobs.append(job)
    diagnostics["snapshotJobs"] = len(snapshot_jobs)
    return snapshot_jobs, diagnostics


def _company_source_reason(
    diagnostics: Mapping[str, Any],
    *,
    had_transient_error: bool = False,
    budget_reached: bool = False,
    all_jobs_already_analyzed: bool = False,
) -> str:
    if budget_reached:
        return "detail_budget_reached"
    if all_jobs_already_analyzed:
        return "all_snapshot_jobs_already_analyzed"
    if had_transient_error and int(diagnostics.get("rawJobsFetched") or 0) <= 0:
        return "transient_fetch_error"
    if int(diagnostics.get("rawJobsFetched") or 0) <= 0:
        return "no_jobs_fetched"
    if int(diagnostics.get("snapshotJobs") or 0) <= 0:
        return "all_jobs_filtered"
    if int(diagnostics.get("selectedJobs") or 0) <= 0:
        return "no_jobs_selected"
    if int(diagnostics.get("queuedJobs") or 0) <= 0:
        return "no_jobs_queued"
    return "queued_jobs"


def _build_completed_job_urls(
    jobs: list[Mapping[str, Any]] | None,
) -> set[str]:
    completed_urls: set[str] = set()
    for job in jobs or []:
        if not isinstance(job, Mapping):
            continue
        url = normalize_job_url(job.get("url") or job.get("canonicalUrl") or "")
        if not url:
            continue
        if analysis_completed(job.get("analysis")):
            completed_urls.add(url)
    return completed_urls


def _commit_company_source_attempt(
    *,
    company: dict[str, Any],
    discovered_now: str,
    snapshot_jobs: list[dict[str, Any]],
    snapshot_new_jobs_count: int,
    diagnostics: Mapping[str, Any],
    coverage_complete: bool,
    source_work_state: Mapping[str, Any] | None,
    job_page_coverage: Mapping[str, Any] | None = None,
) -> None:
    company["lastSearchedAt"] = discovered_now
    company["lastJobsFoundCount"] = len(snapshot_jobs)
    company["lastNewJobsCount"] = snapshot_new_jobs_count
    if snapshot_jobs:
        company["snapshotJobUrls"] = [str(job.get("url") or "").strip() for job in snapshot_jobs]
        company["knownJobUrls"] = merge_unique_strings(
            get_normalized_company_job_url_list(company, "knownJobUrls"),
            company["snapshotJobUrls"],
        )
    else:
        company.pop("snapshotJobUrls", None)
    if isinstance(job_page_coverage, Mapping) and job_page_coverage:
        company["jobPageCoverage"] = dict(job_page_coverage)
    elif job_page_coverage is not None:
        company.pop("jobPageCoverage", None)
    company["snapshotComplete"] = bool(coverage_complete)
    company["sourceDiagnostics"] = dict(diagnostics)
    if source_work_state:
        company["sourceWorkState"] = dict(source_work_state)
    else:
        company["sourceWorkState"] = clear_work_unit_state()
    company.pop("cooldownUntil", None)


def _search_jobs_with_company_fallback(
    *,
    company: Mapping[str, Any],
    company_name: str,
    config: Mapping[str, Any] | None,
    client: ResponseRequestClient | None,
    progress_callback: Callable[[str], None] | None = None,
    job_page_coverage: Mapping[str, Any] | None = None,
    budget: _SourceStageBudget,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if client is None or not company_search_fallback_enabled(company, config):
        return [], {"cacheHit": False}
    fallback_query = build_company_search_fallback_query(company, config)
    if not fallback_query:
        return [], {"cacheHit": False}
    known_job_urls = merge_unique_strings(
        get_normalized_company_job_url_list(company, "knownJobUrls"),
        get_normalized_company_job_url_list(company, "snapshotJobUrls"),
    )
    cache_allowed = not known_job_urls
    cache_key = build_company_search_cache_key(
        company_name=company_name,
        company_website=str(company.get("website") or "").strip(),
        jobs_page_url=str(company.get("jobsPageUrl") or company.get("careersUrl") or "").strip(),
        page_type=str(company.get("jobsPageType") or "").strip().lower(),
        sample_job_urls=(
            [str(item).strip() for item in company.get("sampleJobUrls", []) if str(item).strip()]
            if isinstance(company.get("sampleJobUrls"), list)
            else None
        ),
        known_job_urls=known_job_urls,
        query=fallback_query,
    )
    normalized_coverage = normalize_job_page_coverage_state(job_page_coverage)
    cached_entry = (
        dict(normalized_coverage.get("companySearchCache", {})).get(cache_key)
        if isinstance(normalized_coverage.get("companySearchCache"), Mapping)
        else None
    )
    if cache_allowed and isinstance(cached_entry, Mapping):
        cached_jobs = dedupe_jobs_by_normalized_url(
            [dict(item) for item in cached_entry.get("jobs", []) if isinstance(item, Mapping)]
        )
        return cached_jobs, {
            "cacheHit": True,
            "cacheKey": cache_key,
            "cacheEntry": dict(cached_entry),
        }
    if progress_callback is not None:
        progress_callback(f"Python company fallback: {company_name} | {fallback_query}")
    search_timeout_seconds = budget.capped_timeout(COMPANY_SEARCH_FALLBACK_TIMEOUT_SECONDS)
    with _temporary_client_timeout(client, timeout_seconds=search_timeout_seconds):
        fallback_jobs = openai_search_jobs(
            client,
            config=config,
            company_name=company_name,
            company_website=str(company.get("website") or "").strip(),
            jobs_page_url=str(company.get("jobsPageUrl") or company.get("careersUrl") or "").strip(),
            page_type=str(company.get("jobsPageType") or "").strip().lower(),
            sample_job_urls=(
                [str(item).strip() for item in company.get("sampleJobUrls", []) if str(item).strip()]
                if isinstance(company.get("sampleJobUrls"), list)
                else None
            ),
            known_job_urls=known_job_urls,
            query=fallback_query,
        )
    normalized_jobs = [
        {
            "title": str(job.get("title") or "").strip(),
            "company": str(job.get("company") or company_name).strip(),
            "location": str(job.get("location") or "").strip(),
            "url": str(job.get("url") or "").strip(),
            "datePosted": str(job.get("datePosted") or "").strip(),
            "summary": str(job.get("summary") or "").strip(),
            "availabilityHint": str(job.get("availabilityHint") or "").strip(),
            "source": f"company_search:{company_name}",
            "sourceType": "company_search",
        }
        for job in fallback_jobs
        if isinstance(job, Mapping) and str(job.get("url") or "").strip()
    ]
    deduped_jobs = [
        job
        for job in dedupe_jobs_by_normalized_url(normalized_jobs)
        if normalize_job_url(job.get("url") or "") not in set(known_job_urls)
    ]
    if not cache_allowed or not deduped_jobs:
        return deduped_jobs, {
            "cacheHit": False,
            "cacheKey": cache_key,
            "cacheEntry": None,
        }
    return deduped_jobs, {
        "cacheHit": False,
        "cacheKey": cache_key,
        "cacheEntry": {
            "query": fallback_query,
            "companyWebsite": str(company.get("website") or "").strip(),
            "jobsPageUrl": str(company.get("jobsPageUrl") or company.get("careersUrl") or "").strip(),
            "pageType": str(company.get("jobsPageType") or "").strip().lower(),
            "sampleJobUrls": (
                [str(item).strip() for item in company.get("sampleJobUrls", []) if str(item).strip()]
                if isinstance(company.get("sampleJobUrls"), list)
                else []
            ),
            "jobs": deduped_jobs,
        },
    }


def _mark_ai_prerank_pending_retry(
    *,
    company: dict[str, Any],
    diagnostics: dict[str, Any],
    discovered_now: str,
    snapshot_jobs: list[dict[str, Any]],
    snapshot_new_jobs_count: int,
    error: Exception,
    search_run_id: int | None = None,
    coverage_complete: bool,
    job_page_coverage: Mapping[str, Any] | None = None,
) -> None:
    diagnostics["snapshotJobs"] = len(snapshot_jobs)
    diagnostics["newSnapshotJobs"] = snapshot_new_jobs_count
    diagnostics["selectedJobs"] = 0
    diagnostics["queuedJobs"] = 0
    diagnostics["reason"] = "ai_job_prerank_pending_retry"
    _commit_company_source_attempt(
        company=company,
        discovered_now=discovered_now,
        snapshot_jobs=snapshot_jobs,
        snapshot_new_jobs_count=snapshot_new_jobs_count,
        diagnostics=diagnostics,
        coverage_complete=coverage_complete,
        source_work_state=record_technical_failure(
            company.get("sourceWorkState"),
            run_id=search_run_id,
            reason="ai_job_prerank_pending_retry",
        ),
        job_page_coverage=job_page_coverage,
    )


def collect_supported_company_source_jobs(
    companies: list[Mapping[str, Any]],
    *,
    config: Mapping[str, Any] | None,
    existing_jobs: list[Mapping[str, Any]] | None = None,
    timeout_seconds: int | None = None,
    search_run_id: int | None = None,
    progress_callback: Callable[[str], None] | None = None,
    client: ResponseRequestClient | None = None,
) -> CompanySourcesFetchResult:
    budget = _SourceStageBudget.from_timeout_seconds(timeout_seconds)
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
    completed_job_urls = _build_completed_job_urls(existing_jobs)
    analysis = dict(config.get("analysis") or {}) if isinstance(config, Mapping) else {}
    detail_fetch_cap = max(0, int(_to_number(analysis.get("jdFetchMaxJobsPerRun"), 0)))
    detail_fetch_count = 0
    deferred_supported_companies: list[dict[str, Any]] = []
    generic_candidates: list[tuple[dict[str, Any], bool]] = [
        (dict(item), False) for item in remaining_companies if isinstance(item, Mapping)
    ]

    for index, company in enumerate(supported_companies, start=1):
        if budget.exhausted():
            if progress_callback is not None:
                progress_callback("Python company sources stage budget reached during ATS fetching; deferring remaining companies.")
            deferred_supported_companies.append(dict(company))
            deferred_supported_companies.extend(
                dict(item)
                for item in supported_companies[index:]
                if isinstance(item, Mapping)
            )
            break
        company_name = str(company.get("name") or "").strip() or "Unknown company"
        ats_type = str(company.get("atsType") or "").strip().lower()
        ats_id = str(company.get("atsId") or "").strip()
        diagnostics: dict[str, Any] = {
            "sourcePath": "ats",
            "atsType": ats_type,
            "rawJobsFetched": 0,
            "snapshotJobs": 0,
            "selectedJobs": 0,
            "queuedJobs": 0,
        }
        if progress_callback is not None:
            progress_callback(
                f"Python company sources {index}/{len(supported_companies)}: {company_name} | {ats_type}"
            )
        try:
            raw_jobs = fetch_supported_ats_jobs(
                ats_type,
                ats_id,
                config=config,
                timeout_seconds=budget.remaining_timeout(),
            )
        except Exception:
            company["sourceWorkState"] = record_technical_failure(
                company.get("sourceWorkState"),
                run_id=search_run_id,
                reason="ats_fetch_error",
            )
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
        snapshot_jobs, filter_diagnostics = _prepare_snapshot_jobs(
            normalized_jobs,
            max_post_age_days=max_post_age_days,
        )
        diagnostics.update(filter_diagnostics)
        known_job_urls = set(get_normalized_company_job_url_list(company, "knownJobUrls"))
        snapshot_new_jobs_count = sum(1 for job in snapshot_jobs if job["url"] not in known_job_urls)
        snapshot_jobs, reused_prerank_jobs = overlay_cached_job_prerank_scores(
            snapshot_jobs,
            existing_jobs,
        )
        diagnostics["reusedPrerankJobs"] = reused_prerank_jobs
        try:
            with _temporary_client_timeout(
                client,
                timeout_seconds=budget.capped_timeout(COMPANY_JOB_PRERANK_TIMEOUT_SECONDS),
            ):
                snapshot_jobs = prerank_company_jobs_for_candidate(
                    client,
                    config=config,
                    company=company,
                    jobs=snapshot_jobs,
                )
        except Exception as exc:
            _mark_ai_prerank_pending_retry(
                company=company,
                diagnostics=diagnostics,
                discovered_now=discovered_now,
                snapshot_jobs=snapshot_jobs,
                snapshot_new_jobs_count=snapshot_new_jobs_count,
                error=exc,
                search_run_id=search_run_id,
                coverage_complete=True,
            )
            processed_companies.append(company)
            continue
        coverage_selection = select_company_jobs_for_coverage(
            company=company,
            jobs=snapshot_jobs,
            limit=max_jobs_per_company,
            completed_job_urls=completed_job_urls,
        )
        diagnostics["selectedJobs"] = len(coverage_selection["jobs"])
        diagnostics["completedJobsExcluded"] = int(coverage_selection.get("excludedCompletedCount") or 0)
        diagnostics["lowPrerankJobsExcluded"] = int(coverage_selection.get("excludedLowPrerankCount") or 0)
        diagnostics["pendingPrerankJobsDeferred"] = int(
            coverage_selection.get("excludedPendingPrerankCount") or 0
        )
        budget_reached = False
        try:
            selected_jobs, detail_fetch_count = enrich_selected_jobs_with_details(
                coverage_selection["jobs"],
                config=config,
                client=client,
                timeout_seconds=budget.remaining_timeout(),
                detail_fetch_cap=detail_fetch_cap,
                already_fetched_count=detail_fetch_count,
                progress_callback=progress_callback,
            )
        except TimeoutError:
            if progress_callback is not None:
                progress_callback(
                    f"Python company sources budget reached during ATS detail enrichment: {company_name}. Keeping fetched jobs and deferring remaining companies."
                )
            selected_jobs = list(coverage_selection["jobs"])
            budget_reached = True
        diagnostics["queuedJobs"] = len(selected_jobs)
        diagnostics["newSnapshotJobs"] = snapshot_new_jobs_count
        diagnostics["reason"] = _company_source_reason(
            diagnostics,
            budget_reached=budget_reached,
            all_jobs_already_analyzed=(
                diagnostics["selectedJobs"] <= 0
                and diagnostics["completedJobsExcluded"] >= diagnostics["snapshotJobs"]
                and diagnostics["lowPrerankJobsExcluded"] <= 0
                and diagnostics["snapshotJobs"] > 0
            ),
        )
        if coverage_selection["changed"]:
            if coverage_selection["jobLinkCoverage"]:
                company["jobLinkCoverage"] = coverage_selection["jobLinkCoverage"]
            else:
                company.pop("jobLinkCoverage", None)
        _commit_company_source_attempt(
            company=company,
            discovered_now=discovered_now,
            snapshot_jobs=snapshot_jobs,
            snapshot_new_jobs_count=snapshot_new_jobs_count,
            diagnostics=diagnostics,
            coverage_complete=True,
            source_work_state={},
        )
        processed_companies.append(company)
        fetched_jobs.extend(selected_jobs)
        if budget_reached:
            deferred_supported_companies.extend(
                dict(item)
                for item in supported_companies[index:]
                if isinstance(item, Mapping)
            )
            break

    generic_remaining: list[dict[str, Any]] = list(deferred_supported_companies)
    for index, (company, inherited_transient_error) in enumerate(generic_candidates, start=1):
        if budget.exhausted():
            if progress_callback is not None:
                progress_callback("Python company sources stage budget reached during company-page fetching; deferring remaining companies.")
            generic_remaining.append(dict(company))
            generic_remaining.extend(
                dict(item)
                for item, _ in generic_candidates[index:]
                if isinstance(item, Mapping)
            )
            break
        company_name = str(company.get("name") or "").strip() or "Unknown company"
        careers_url = str(company.get("careersUrl") or company.get("jobsPageUrl") or "").strip()
        website = str(company.get("website") or "").strip()
        jobs_page_type = str(company.get("jobsPageType") or "").strip().lower()
        previous_snapshot_complete = company_source_coverage_complete(company)
        preserved_job_page_coverage = normalize_job_page_coverage_state(company.get("jobPageCoverage"))
        job_page_coverage = (
            {
                key: value
                for key, value in preserved_job_page_coverage.items()
                if key in {"listingPageCache", "companySearchCache"}
            }
            if previous_snapshot_complete
            else preserved_job_page_coverage
        )
        careers_discovery_cache = normalize_company_careers_discovery_cache(company.get("careersDiscoveryCache"))
        had_transient_error = inherited_transient_error
        fallback_query = build_company_search_fallback_query(company, config)
        diagnostics: dict[str, Any] = {
            "sourcePath": "company_page",
            "websiteResolved": 1 if website else 0,
            "careersUrlResolved": 1 if careers_url else 0,
            "jobsPageType": jobs_page_type,
            "usedCompanySearch": 0,
            "cachedCompanySearchReused": 0,
            "cachedCareersDiscoveryReused": 0,
            "followedBoardLinks": 0,
            "fallbackQuery": fallback_query,
            "listingPagesProcessed": 0,
            "pendingListingPages": len(job_page_coverage.get("pendingListingUrls") or []),
            "rawJobsFetched": 0,
            "snapshotJobs": 0,
            "selectedJobs": 0,
            "queuedJobs": 0,
        }
        company["snapshotComplete"] = False
        company.pop("cooldownUntil", None)
        if careers_discovery_cache:
            if careers_discovery_cache.get("website") and not website:
                company["website"] = str(careers_discovery_cache.get("website") or "").strip()
                website = str(company.get("website") or "").strip()
                diagnostics["websiteResolved"] = 1
            if careers_discovery_cache.get("jobsPageUrl") and not careers_url:
                cached_jobs_page_url = str(careers_discovery_cache.get("jobsPageUrl") or "").strip()
                company["jobsPageUrl"] = cached_jobs_page_url
                company["careersUrl"] = cached_jobs_page_url
                careers_url = cached_jobs_page_url
                diagnostics["careersUrlResolved"] = 1
            if careers_discovery_cache.get("sampleJobUrls"):
                company["sampleJobUrls"] = merge_unique_strings(
                    company.get("sampleJobUrls"),
                    careers_discovery_cache.get("sampleJobUrls"),
                )
            cached_page_type = str(careers_discovery_cache.get("pageType") or "").strip().lower()
            if cached_page_type:
                jobs_page_type = cached_page_type
                company["jobsPageType"] = cached_page_type
                diagnostics["jobsPageType"] = cached_page_type
            diagnostics["cachedCareersDiscoveryReused"] = 1
        if (not website or not careers_url) and can_use_web_search and company_name and not careers_discovery_cache:
            try:
                with _temporary_client_timeout(
                    client,
                    timeout_seconds=budget.capped_timeout(COMPANY_CAREERS_DISCOVERY_TIMEOUT_SECONDS),
                ):
                    discovered = discover_company_careers(
                        client,
                        config=config,
                        company_name=company_name,
                    )
                company["careersDiscoveryCache"] = normalize_company_careers_discovery_cache(discovered)
                if discovered.get("website") and not website:
                    company["website"] = str(discovered.get("website") or "").strip()
                    website = str(company.get("website") or "").strip()
                    diagnostics["websiteResolved"] = 1
                if discovered.get("jobsPageUrl") and not careers_url:
                    company["jobsPageUrl"] = str(discovered.get("jobsPageUrl") or "").strip()
                    company["careersUrl"] = str(discovered.get("jobsPageUrl") or "").strip()
                    careers_url = str(company.get("careersUrl") or "").strip()
                    diagnostics["careersUrlResolved"] = 1
                if isinstance(discovered.get("sampleJobUrls"), list):
                    company["sampleJobUrls"] = merge_unique_strings(
                        company.get("sampleJobUrls"),
                        discovered.get("sampleJobUrls"),
                    )
                jobs_page_type = str(discovered.get("pageType") or jobs_page_type or "").strip().lower()
                if jobs_page_type:
                    company["jobsPageType"] = jobs_page_type
                    diagnostics["jobsPageType"] = jobs_page_type
            except Exception:
                had_transient_error = True
        raw_jobs: list[dict[str, Any]] = []
        processed_listing_urls: list[str] = []
        discovered_listing_urls: list[str] = []
        listing_page_cache_updates: dict[str, dict[str, Any]] = {}
        if not raw_jobs and careers_url:
            if progress_callback is not None:
                progress_callback(
                    f"Python company pages {index}/{len(generic_candidates)}: {company_name} | {jobs_page_type or 'careers_page'}"
                )
            materialized_job_urls = merge_unique_strings(
                get_normalized_company_job_url_list(company, "knownJobUrls"),
                get_normalized_company_job_url_list(company, "snapshotJobUrls"),
            )
            listing_urls = select_listing_urls_for_processing(
                entry_url=careers_url,
                coverage_state=job_page_coverage,
                limit=NON_ATS_LISTING_PAGES_PER_RUN,
                allow_entry_retry_when_coverage_complete=not materialized_job_urls,
            )
            diagnostics["listingPagesProcessed"] = len(listing_urls)
            try:
                aggregated_jobs: list[dict[str, Any]] = []
                for listing_url in listing_urls:
                    seen_job_urls_for_listing = merge_unique_strings(
                        materialized_job_urls,
                        [str(job.get("url") or "").strip() for job in aggregated_jobs],
                    )
                    listing_timeout_seconds = budget.capped_timeout(
                        COMPANY_CAREERS_PAGE_FETCH_TIMEOUT_SECONDS
                    )
                    with _temporary_client_timeout(
                        client,
                        timeout_seconds=listing_timeout_seconds,
                    ):
                        careers_trace = fetch_careers_page_jobs_with_trace(
                            listing_url,
                            config=config,
                            timeout_seconds=listing_timeout_seconds,
                            sample_job_urls=(
                                [str(item).strip() for item in company.get("sampleJobUrls", []) if str(item).strip()]
                                if isinstance(company.get("sampleJobUrls"), list)
                                else None
                            ),
                            seen_job_urls=seen_job_urls_for_listing,
                            client=client,
                            company_name=company_name,
                            listing_page_cache_entry=(
                                dict(job_page_coverage.get("listingPageCache", {})).get(normalize_job_url(listing_url))
                                if isinstance(job_page_coverage.get("listingPageCache"), Mapping)
                                else None
                            ),
                        )
                    aggregated_jobs.extend(careers_trace.jobs)
                    processed_listing_urls.append(normalize_job_url(listing_url))
                    listing_page_cache_entry = getattr(careers_trace, "listing_page_cache_entry", None)
                    if isinstance(listing_page_cache_entry, Mapping):
                        listing_page_cache_updates[normalize_job_url(listing_url)] = dict(listing_page_cache_entry)
                    discovered_listing_urls = merge_unique_strings(
                        discovered_listing_urls,
                        careers_trace.next_listing_urls,
                    )
                    diagnostics["followedBoardLinks"] = int(diagnostics.get("followedBoardLinks") or 0) + len(
                        careers_trace.followed_links
                    )
                    if careers_trace.source_path.endswith("_cache"):
                        diagnostics["cachedListingPagesReused"] = int(
                            diagnostics.get("cachedListingPagesReused") or 0
                        ) + 1
                    if careers_trace.source_path:
                        diagnostics["sourcePath"] = careers_trace.source_path
                    elif jobs_page_type:
                        diagnostics["sourcePath"] = jobs_page_type
                raw_jobs = dedupe_jobs_by_normalized_url(aggregated_jobs)
            except Exception:
                had_transient_error = True
                raw_jobs = []
        if not raw_jobs and can_use_web_search and not discovered_listing_urls:
            try:
                diagnostics["usedCompanySearch"] = 1
                diagnostics["sourcePath"] = "company_search"
                raw_jobs, company_search_cache = _search_jobs_with_company_fallback(
                    company=company,
                    company_name=company_name,
                    config=config,
                    client=client,
                    progress_callback=progress_callback,
                    job_page_coverage=job_page_coverage,
                    budget=budget,
                )
                if company_search_cache.get("cacheHit"):
                    diagnostics["cachedCompanySearchReused"] = 1
                elif company_search_cache.get("cacheKey") and isinstance(company_search_cache.get("cacheEntry"), Mapping):
                    company_search_state = dict(job_page_coverage.get("companySearchCache") or {})
                    company_search_state[str(company_search_cache["cacheKey"])] = dict(company_search_cache["cacheEntry"])
                    job_page_coverage["companySearchCache"] = company_search_state
            except Exception:
                had_transient_error = True
        known_job_urls = set(get_normalized_company_job_url_list(company, "knownJobUrls"))
        snapshot_new_jobs_count = sum(
            1 for job in raw_jobs if normalize_job_url(job.get("url") or "") not in known_job_urls
        )
        job_page_coverage = update_job_page_coverage_state(
            entry_url=careers_url,
            coverage_state=job_page_coverage,
            processed_listing_urls=processed_listing_urls,
            discovered_listing_urls=discovered_listing_urls,
            listing_page_cache_updates=listing_page_cache_updates,
        )
        diagnostics["pendingListingPages"] = len(job_page_coverage.get("pendingListingUrls") or [])
        if not raw_jobs:
            diagnostics["reason"] = (
                "no_careers_page"
                if (
                    not diagnostics["careersUrlResolved"]
                    and not diagnostics["usedCompanySearch"]
                    and not had_transient_error
                )
                else (
                    "listing_frontier_pending"
                    if not job_page_coverage.get("coverageComplete")
                    else _company_source_reason(diagnostics, had_transient_error=had_transient_error)
                )
            )
            next_work_state = (
                record_technical_failure(
                    company.get("sourceWorkState"),
                    run_id=search_run_id,
                    reason=str(diagnostics.get("reason") or "transient_fetch_error"),
                )
                if had_transient_error
                else {}
            )
            coverage_complete = bool(job_page_coverage.get("coverageComplete")) and not had_transient_error
            _commit_company_source_attempt(
                company=company,
                discovered_now=discovered_now,
                snapshot_jobs=[],
                snapshot_new_jobs_count=0,
                diagnostics=diagnostics,
                coverage_complete=coverage_complete,
                source_work_state=next_work_state,
                job_page_coverage=job_page_coverage,
            )
            if not coverage_complete:
                generic_remaining.append(company)
            else:
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
        snapshot_jobs, filter_diagnostics = _prepare_snapshot_jobs(
            normalized_jobs,
            max_post_age_days=max_post_age_days,
        )
        diagnostics.update(filter_diagnostics)
        if not snapshot_jobs and can_use_web_search and not diagnostics["usedCompanySearch"]:
            try:
                diagnostics["usedCompanySearch"] = 1
                diagnostics["sourcePath"] = "company_search"
                raw_jobs, company_search_cache = _search_jobs_with_company_fallback(
                    company=company,
                    company_name=company_name,
                    config=config,
                    client=client,
                    progress_callback=progress_callback,
                    job_page_coverage=job_page_coverage,
                    budget=budget,
                )
                if company_search_cache.get("cacheHit"):
                    diagnostics["cachedCompanySearchReused"] = 1
                elif company_search_cache.get("cacheKey") and isinstance(company_search_cache.get("cacheEntry"), Mapping):
                    company_search_state = dict(job_page_coverage.get("companySearchCache") or {})
                    company_search_state[str(company_search_cache["cacheKey"])] = dict(company_search_cache["cacheEntry"])
                    job_page_coverage["companySearchCache"] = company_search_state
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
                snapshot_jobs, filter_diagnostics = _prepare_snapshot_jobs(
                    normalized_jobs,
                    max_post_age_days=max_post_age_days,
                )
                diagnostics.update(filter_diagnostics)
            except Exception:
                had_transient_error = True
        snapshot_new_jobs_count = sum(1 for job in snapshot_jobs if job["url"] not in known_job_urls)
        snapshot_jobs, reused_prerank_jobs = overlay_cached_job_prerank_scores(
            snapshot_jobs,
            existing_jobs,
        )
        diagnostics["reusedPrerankJobs"] = reused_prerank_jobs
        try:
            with _temporary_client_timeout(
                client,
                timeout_seconds=budget.capped_timeout(COMPANY_JOB_PRERANK_TIMEOUT_SECONDS),
            ):
                snapshot_jobs = prerank_company_jobs_for_candidate(
                    client,
                    config=config,
                    company=company,
                    jobs=snapshot_jobs,
                )
        except Exception as exc:
            _mark_ai_prerank_pending_retry(
                company=company,
                diagnostics=diagnostics,
                discovered_now=discovered_now,
                snapshot_jobs=snapshot_jobs,
                snapshot_new_jobs_count=snapshot_new_jobs_count,
                error=exc,
                search_run_id=search_run_id,
                coverage_complete=bool(job_page_coverage.get("coverageComplete")),
                job_page_coverage=job_page_coverage,
            )
            processed_companies.append(company)
            continue
        if not snapshot_jobs:
            diagnostics["reason"] = (
                "listing_frontier_pending"
                if not job_page_coverage.get("coverageComplete")
                else _company_source_reason(
                    diagnostics,
                    had_transient_error=had_transient_error,
                )
            )
            next_work_state = (
                record_technical_failure(
                    company.get("sourceWorkState"),
                    run_id=search_run_id,
                    reason=str(diagnostics.get("reason") or "transient_fetch_error"),
                )
                if had_transient_error
                else {}
            )
            coverage_complete = bool(job_page_coverage.get("coverageComplete")) and not had_transient_error
            _commit_company_source_attempt(
                company=company,
                discovered_now=discovered_now,
                snapshot_jobs=[],
                snapshot_new_jobs_count=0,
                diagnostics=diagnostics,
                coverage_complete=coverage_complete,
                source_work_state=next_work_state,
                job_page_coverage=job_page_coverage,
            )
            if coverage_complete:
                processed_companies.append(company)
            else:
                generic_remaining.append(company)
            continue
        company["snapshotComplete"] = False
        company.pop("cooldownUntil", None)
        coverage_selection = select_company_jobs_for_coverage(
            company=company,
            jobs=snapshot_jobs,
            limit=max_jobs_per_company,
            completed_job_urls=completed_job_urls,
        )
        diagnostics["selectedJobs"] = len(coverage_selection["jobs"])
        diagnostics["completedJobsExcluded"] = int(coverage_selection.get("excludedCompletedCount") or 0)
        diagnostics["lowPrerankJobsExcluded"] = int(coverage_selection.get("excludedLowPrerankCount") or 0)
        diagnostics["pendingPrerankJobsDeferred"] = int(
            coverage_selection.get("excludedPendingPrerankCount") or 0
        )
        budget_reached = False
        try:
            selected_jobs, detail_fetch_count = enrich_selected_jobs_with_details(
                coverage_selection["jobs"],
                config=config,
                client=client,
                timeout_seconds=budget.remaining_timeout(),
                detail_fetch_cap=detail_fetch_cap,
                already_fetched_count=detail_fetch_count,
                progress_callback=progress_callback,
            )
        except TimeoutError:
            if progress_callback is not None:
                progress_callback(
                    f"Python company sources budget reached during company detail enrichment: {company_name}. Keeping fetched jobs and deferring remaining companies."
                )
            selected_jobs = list(coverage_selection["jobs"])
            budget_reached = True
        diagnostics["queuedJobs"] = len(selected_jobs)
        diagnostics["newSnapshotJobs"] = snapshot_new_jobs_count
        diagnostics["reason"] = _company_source_reason(
            diagnostics,
            had_transient_error=had_transient_error,
            budget_reached=budget_reached,
            all_jobs_already_analyzed=(
                diagnostics["selectedJobs"] <= 0
                and diagnostics["completedJobsExcluded"] >= diagnostics["snapshotJobs"]
                and diagnostics["lowPrerankJobsExcluded"] <= 0
                and diagnostics["snapshotJobs"] > 0
            ),
        )
        if not job_page_coverage.get("coverageComplete") and not budget_reached:
            diagnostics["reason"] = "listing_frontier_pending"
        if coverage_selection["changed"]:
            if coverage_selection["jobLinkCoverage"]:
                company["jobLinkCoverage"] = coverage_selection["jobLinkCoverage"]
            else:
                company.pop("jobLinkCoverage", None)
        next_work_state = (
            record_technical_failure(
                company.get("sourceWorkState"),
                run_id=search_run_id,
                reason=str(diagnostics.get("reason") or "transient_fetch_error"),
            )
            if had_transient_error
            else {}
        )
        coverage_complete = bool(job_page_coverage.get("coverageComplete")) and not had_transient_error
        _commit_company_source_attempt(
            company=company,
            discovered_now=discovered_now,
            snapshot_jobs=snapshot_jobs,
            snapshot_new_jobs_count=snapshot_new_jobs_count,
            diagnostics=diagnostics,
            coverage_complete=coverage_complete,
            source_work_state=next_work_state,
            job_page_coverage=job_page_coverage,
        )
        if coverage_complete:
            processed_companies.append(company)
        else:
            generic_remaining.append(company)
        fetched_jobs.extend(selected_jobs)
        if budget_reached:
            generic_remaining.extend(
                dict(item)
                for item, _ in generic_candidates[index:]
                if isinstance(item, Mapping)
            )
            break

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
    client: ResponseRequestClient | None = None,
    timeout_seconds: int | None,
) -> dict[str, Any]:
    from .company_sources_enrichment import enrich_job_with_details as _enrich_job_with_details

    return _enrich_job_with_details(
        job,
        config=config,
        client=client,
        timeout_seconds=timeout_seconds,
        details_fetcher=fetch_job_details,
    )


def enrich_selected_jobs_with_details(
    jobs: list[Mapping[str, Any]],
    *,
    config: Mapping[str, Any] | None,
    client: ResponseRequestClient | None = None,
    timeout_seconds: int | None,
    detail_fetch_cap: int,
    already_fetched_count: int,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    from .company_sources_enrichment import enrich_selected_jobs_with_details as _enrich_selected_jobs_with_details

    return _enrich_selected_jobs_with_details(
        jobs,
        config=config,
        client=client,
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
