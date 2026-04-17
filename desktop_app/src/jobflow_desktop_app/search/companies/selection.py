from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .discovery import company_domain, normalize_company_name


def _to_number(value: object, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number else fallback


@dataclass(frozen=True)
class NormalizedSupportConfig:
    priority_region_weights: dict[str, float]


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


def company_matches_major_keyword(company: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    keywords = _major_company_keywords(config)
    name = str(company.get("name") or "").strip().casefold()
    if not name or not keywords:
        return False
    return any(keyword in name for keyword in keywords)


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


def company_lifecycle_bucket(company: Mapping[str, Any]) -> int:
    pending_analysis_count = max(
        0,
        int(_to_number(company.get("snapshotPendingAnalysisCount"), 0)),
    )
    if pending_analysis_count > 0:
        return 0
    if company.get("snapshotComplete") is False:
        return 1
    return 2


def _major_company_keywords(config: Mapping[str, Any]) -> tuple[str, ...]:
    sources = config.get("sources")
    if not isinstance(sources, Mapping):
        return ()
    return tuple(
        str(item or "").strip().casefold()
        for item in sources.get("majorCompanyKeywords", [])
        if str(item or "").strip()
    )


def _normalized_support_config(config: Mapping[str, Any]) -> NormalizedSupportConfig:
    sources = config.get("sources")
    if not isinstance(sources, Mapping):
        return NormalizedSupportConfig(
            priority_region_weights={},
        )
    region_weights: dict[str, float] = {}
    raw_region_weights = sources.get("priorityRegionWeights")
    if isinstance(raw_region_weights, Mapping):
        for key, value in raw_region_weights.items():
            text_key = str(key or "").strip().casefold()
            if not text_key:
                continue
            weight = _to_number(value, 0.0)
            if weight != 0:
                region_weights[text_key] = weight
    return NormalizedSupportConfig(
        priority_region_weights=region_weights,
    )


def _company_support_score(company: Mapping[str, Any], config: NormalizedSupportConfig) -> float:
    tags = [
        str(item or "").strip().casefold()
        for item in company.get("tags", [])
        if str(item or "").strip()
    ]
    region_weight = (
        max((config.priority_region_weights.get(tag, 0.0) for tag in tags), default=0.0)
        if config.priority_region_weights
        else 0.0
    )
    manual_priority = _to_number(company.get("priority"), 0.0)
    return manual_priority + region_weight


def _prioritize_companies_for_run(
    companies: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not companies:
        return []

    support_config = _normalized_support_config(config)
    scored: list[dict[str, Any]] = []
    for index, company in enumerate(companies):
        pending_analysis_count = max(0, int(_to_number(company.get("snapshotPendingAnalysisCount"), 0)))
        scored.append(
            {
                "company": company,
                "index": index,
                "lifecycleBucket": company_lifecycle_bucket(company),
                "pendingAnalysisCount": pending_analysis_count,
                "supportScore": _company_support_score(company, support_config),
            }
        )
    scored.sort(
        key=lambda item: (
            item["lifecycleBucket"],
            -item["pendingAnalysisCount"],
            -item["supportScore"],
            item["index"],
        )
    )
    return scored


def _build_company_run_selection(
    ranked_companies: list[dict[str, Any]],
    max_companies: int,
    config: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    limit = max(0, int(max_companies))
    if not ranked_companies or limit <= 0:
        return []
    companies = [item["company"] for item in ranked_companies]
    if len(companies) <= limit:
        return companies[:limit]
    sources = config.get("sources") if isinstance(config.get("sources"), Mapping) else {}
    pinned: list[dict[str, Any]] = []
    tail_start = 0
    for index, item in enumerate(ranked_companies):
        if int(item["lifecycleBucket"]) < 2:
            pinned.append(item["company"])
            tail_start = index + 1
            continue
        break
    pinned = pinned[:limit]
    tail = companies[tail_start:]
    remaining_slots = limit - len(pinned)
    if remaining_slots <= 0 or len(tail) <= remaining_slots:
        return companies[:limit]
    interval_days = int(_to_number(sources.get("companyRotationIntervalDays"), 1))
    if interval_days <= 0:
        return companies[:limit]
    rotation_seed = max(0, int(_to_number(sources.get("companyRotationSeed"), 0)))
    reference = now or datetime.now(timezone.utc)
    utc_day = int(reference.timestamp() // (24 * 60 * 60))
    rotation_index = utc_day // interval_days
    rotation_offset = (rotation_index * remaining_slots + rotation_seed) % len(tail)
    rotated_tail = tail[rotation_offset:] + tail[:rotation_offset]
    return pinned + rotated_tail[:remaining_slots]


def select_companies_for_run(
    *,
    config: Mapping[str, Any],
    companies: list[dict[str, Any]],
    max_companies: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    eligible = [
        company
        for company in companies
        if not is_company_in_cooldown(company, now=now)
    ]
    ranked = _prioritize_companies_for_run(eligible, config)
    return _build_company_run_selection(ranked, max_companies, config, now=now)


__all__ = [
    "company_has_region_tag",
    "company_lifecycle_bucket",
    "company_matches_major_keyword",
    "company_record_key",
    "is_company_in_cooldown",
    "select_companies_for_run",
]
