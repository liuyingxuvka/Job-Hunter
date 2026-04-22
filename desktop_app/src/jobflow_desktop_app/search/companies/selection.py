from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .discovery import company_domain, normalize_company_name
from .ranking_thresholds import COMPANY_FIT_MIN_SCORE
from .state import (
    company_has_materialized_jobs_entry,
    company_has_started_source_work,
    company_has_unfinished_source_work,
    company_pending_analysis_count,
    company_source_lifecycle_bucket,
)
from ..state.work_unit_state import (
    is_abandoned,
    is_suspended_for_run,
    normalize_work_unit_state,
)


def _to_number(value: object, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number else fallback


def _company_has_fit_score(company: Mapping[str, Any]) -> bool:
    value = company.get("aiCompanyFitScore")
    try:
        score = float(value)
    except (TypeError, ValueError):
        return False
    return score == score


def company_record_key(company: Mapping[str, Any]) -> str:
    website = str(company.get("website") or "").strip()
    domain = company_domain(website)
    if domain:
        return f"domain:{domain}"
    jurisdiction = str(company.get("jurisdictionCode") or "").strip().casefold()
    company_number = str(company.get("companyNumber") or "").strip().casefold()
    if jurisdiction and company_number:
        return f"registry:{jurisdiction}:{company_number}"
    name = normalize_company_name(company.get("name") or "")
    if name:
        return f"name:{name}"
    return ""


def is_company_in_cooldown(company: Mapping[str, Any], now: datetime | None = None) -> bool:
    text = str(company.get("cooldownUntil") or "").strip()
    if not text:
        return False
    try:
        cooldown_until = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    reference = now or datetime.now(timezone.utc)
    if cooldown_until.tzinfo is None:
        cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
    return cooldown_until > reference


def company_has_region_tag(company: Mapping[str, Any], region_tag: object) -> bool:
    target = str(region_tag or "").strip().casefold()
    if not target:
        return False
    tags = [
        str(item or "").strip().casefold()
        for item in company.get("tags", [])
        if str(item or "").strip()
    ]
    return target in tags


def _company_ranking_work_state(company: Mapping[str, Any]) -> dict[str, object]:
    return normalize_work_unit_state(company.get("rankingWorkState"))


def _prioritize_companies_for_run(
    companies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not companies:
        return []
    scored: list[dict[str, Any]] = []
    for index, company in enumerate(companies):
        pending_analysis_count = company_pending_analysis_count(company)
        scored.append(
            {
                "company": company,
                "index": index,
                "lifecycleBucket": company_source_lifecycle_bucket(company),
                "pendingAnalysisCount": pending_analysis_count,
                "hasJobsEntry": company_has_materialized_jobs_entry(company),
                "aiCompanyFitScore": _to_number(company.get("aiCompanyFitScore"), 0.0),
            }
        )
    scored.sort(
        key=lambda item: (
            item["lifecycleBucket"],
            -item["pendingAnalysisCount"],
            -item["aiCompanyFitScore"],
            -int(item["hasJobsEntry"]),
            item["index"],
        )
    )
    return scored


def unresolved_company_ranking_count(
    companies: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    current_run_id: int | None = None,
) -> int:
    return sum(
        1
        for company in companies
        if not is_company_in_cooldown(company, now=now)
        and not is_abandoned(company.get("sourceWorkState"))
        and not is_abandoned(_company_ranking_work_state(company))
        and not is_suspended_for_run(_company_ranking_work_state(company), current_run_id)
        and not _company_has_fit_score(company)
    )


def select_companies_for_run(
    *,
    companies: list[dict[str, Any]],
    max_companies: int,
    now: datetime | None = None,
    current_run_id: int | None = None,
) -> list[dict[str, Any]]:
    if unresolved_company_ranking_count(
        companies,
        now=now,
        current_run_id=current_run_id,
    ) > 0:
        return []
    eligible = [
        company
        for company in companies
        if not is_company_in_cooldown(company, now=now)
        and not is_abandoned(company.get("sourceWorkState"))
        and not is_abandoned(_company_ranking_work_state(company))
        and not is_suspended_for_run(company.get("sourceWorkState"), current_run_id)
        and (
            company_pending_analysis_count(company) > 0
            or (_company_has_fit_score(company) and _to_number(company.get("aiCompanyFitScore"), 0.0) >= COMPANY_FIT_MIN_SCORE)
        )
    ]
    ranked = _prioritize_companies_for_run(eligible)
    limit = max(0, int(max_companies))
    if limit <= 0:
        return []
    return [item["company"] for item in ranked[:limit]]


__all__ = [
    "company_has_region_tag",
    "company_record_key",
    "is_company_in_cooldown",
    "unresolved_company_ranking_count",
    "select_companies_for_run",
]
