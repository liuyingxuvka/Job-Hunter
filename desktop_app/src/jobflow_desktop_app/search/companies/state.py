from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from ..output.final_output import normalize_job_url
from ..run_state import analysis_completed
from ..state.work_unit_state import normalize_work_unit_state

MAX_COMPANY_COOLDOWN_DAYS = 28
NO_NEW_JOB_STREAK_KEY = "noNewJobCooldownStreak"
COOLDOWN_APPLIED_AT_KEY = "cooldownAppliedAt"


def get_company_cooldown_until(
    adaptive_search: Mapping[str, Any] | None,
    *,
    jobs_found_count: int,
    new_jobs_count: int,
    no_new_job_streak: int = 1,
    now: datetime | None = None,
) -> str:
    del jobs_found_count
    current = now or datetime.now(timezone.utc)
    cooldown_base_days = max(
        1,
        int(_to_number(adaptive_search, "cooldownBaseDays", default=7)),
    )
    with_new_days = max(1, cooldown_base_days // 3)
    if new_jobs_count > 0:
        days = with_new_days
    else:
        days = min(
            MAX_COMPANY_COOLDOWN_DAYS,
            max(1, int(no_new_job_streak)) * cooldown_base_days,
        )
    return (current + timedelta(days=days)).replace(microsecond=0).isoformat()


def company_pending_analysis_count(company: Mapping[str, Any]) -> int:
    return max(0, int(_coerce_int(company.get("snapshotPendingAnalysisCount"))))


def company_source_coverage_complete(company: Mapping[str, Any]) -> bool:
    coverage_state = company.get("jobPageCoverage")
    if isinstance(coverage_state, Mapping) and "coverageComplete" in coverage_state:
        return bool(coverage_state.get("coverageComplete"))
    return company.get("snapshotComplete") is True


def company_has_materialized_jobs_entry(company: Mapping[str, Any]) -> bool:
    jobs_page_url = str(company.get("jobsPageUrl") or company.get("careersUrl") or "").strip()
    if jobs_page_url:
        return True
    sample_job_urls = company.get("sampleJobUrls")
    if isinstance(sample_job_urls, list) and any(str(item or "").strip() for item in sample_job_urls):
        return True
    careers_cache = company.get("careersDiscoveryCache")
    if not isinstance(careers_cache, Mapping):
        return False
    if str(careers_cache.get("jobsPageUrl") or careers_cache.get("careersUrl") or "").strip():
        return True
    cache_samples = careers_cache.get("sampleJobUrls")
    return isinstance(cache_samples, list) and any(str(item or "").strip() for item in cache_samples)


def _pending_listing_urls(company: Mapping[str, Any]) -> list[object]:
    coverage_state = company.get("jobPageCoverage")
    if not isinstance(coverage_state, Mapping):
        return []
    return list(coverage_state.get("pendingListingUrls") or [])


def _source_work_state(company: Mapping[str, Any]) -> dict[str, object]:
    return normalize_work_unit_state(company.get("sourceWorkState"))


def _source_work_reason_counts_as_unfinished(reason: object) -> bool:
    text = str(reason or "").strip().casefold()
    if not text:
        return False
    return text == "ai_job_prerank_pending_retry"


def company_has_unfinished_source_work(company: Mapping[str, Any]) -> bool:
    coverage_state = company.get("jobPageCoverage")
    if isinstance(coverage_state, Mapping):
        if _pending_listing_urls(company):
            return True
        if coverage_state.get("coverageComplete") is False:
            return True
    source_work_state = _source_work_state(company)
    if (
        source_work_state
        and not bool(source_work_state.get("abandoned"))
        and _source_work_reason_counts_as_unfinished(source_work_state.get("lastFailureReason"))
    ):
        return True
    return False


def company_has_started_source_work(company: Mapping[str, Any]) -> bool:
    if str(company.get("lastSearchedAt") or "").strip():
        return True
    if _pending_listing_urls(company):
        return True
    coverage_state = company.get("jobPageCoverage")
    if isinstance(coverage_state, Mapping) and "coverageComplete" in coverage_state:
        return True
    if _source_work_state(company):
        return True
    if company_source_coverage_complete(company):
        return True
    return False


def company_source_lifecycle_bucket(company: Mapping[str, Any]) -> int:
    pending_analysis_count = company_pending_analysis_count(company)
    if pending_analysis_count > 0:
        return 0
    if company_has_unfinished_source_work(company):
        return 1
    if not company_has_started_source_work(company):
        return 2
    return 3


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
            if job is not None and not analysis_completed(job.get("analysis")):
                pending_urls.add(url)
        for url in known_job_urls:
            job = existing_by_url.get(url)
            if job is not None and not analysis_completed(job.get("analysis")):
                pending_urls.add(url)

        pending_analysis_count = len(pending_urls)
        previous_pending = company_pending_analysis_count(item)
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

        source_work_state = _source_work_state(item)
        if source_work_state and not bool(source_work_state.get("abandoned")):
            if "cooldownUntil" in item:
                del item["cooldownUntil"]
                changed = True
            continue

        if company_source_coverage_complete(item):
            last_searched_at = str(item.get("lastSearchedAt") or "").strip()
            cooldown_applied_at = str(item.get(COOLDOWN_APPLIED_AT_KEY) or "").strip()
            new_jobs_count = max(0, int(_coerce_int(item.get("lastNewJobsCount"))))
            no_new_job_streak = max(0, int(_coerce_int(item.get(NO_NEW_JOB_STREAK_KEY))))
            if last_searched_at and cooldown_applied_at != last_searched_at:
                next_streak = 0 if new_jobs_count > 0 else (no_new_job_streak + 1)
                if int(_coerce_int(item.get(NO_NEW_JOB_STREAK_KEY))) != next_streak:
                    item[NO_NEW_JOB_STREAK_KEY] = next_streak
                    changed = True
                item[COOLDOWN_APPLIED_AT_KEY] = last_searched_at
                changed = True
                no_new_job_streak = next_streak
            next_cooldown = get_company_cooldown_until(
                adaptive_search if isinstance(adaptive_search, Mapping) else {},
                jobs_found_count=max(
                    0,
                    int(_coerce_int(item.get("lastJobsFoundCount"), default=len(snapshot_urls))),
                ),
                new_jobs_count=new_jobs_count,
                no_new_job_streak=max(1, no_new_job_streak),
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
    "company_has_materialized_jobs_entry",
    "company_has_started_source_work",
    "company_source_coverage_complete",
    "company_has_unfinished_source_work",
    "company_pending_analysis_count",
    "company_source_lifecycle_bucket",
    "get_company_cooldown_until",
    "reconcile_company_pipeline_state_in_memory",
]
