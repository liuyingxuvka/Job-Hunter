from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .sources_fetchers import (
    fetch_greenhouse_jobs,
    fetch_lever_jobs,
    fetch_smartrecruiters_jobs,
    fetch_supported_ats_jobs,
)
from .sources_helpers import SUPPORTED_DIRECT_ATS_TYPES, detect_ats_from_url


def partition_supported_companies(
    companies: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    supported: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for raw_company in companies:
        company = dict(raw_company)
        ats_type, ats_id = resolve_supported_company_ats(company)
        if not ats_type or not ats_id:
            remaining.append(company)
            continue
        company["atsType"] = ats_type
        company["atsId"] = ats_id
        supported.append(company)
    return supported, remaining


def resolve_supported_company_ats(company: Mapping[str, Any]) -> tuple[str, str]:
    ats_type = str(company.get("atsType") or "").strip().lower()
    ats_id = str(company.get("atsId") or "").strip()
    if ats_type in SUPPORTED_DIRECT_ATS_TYPES and ats_id:
        return ats_type, ats_id
    detected = detect_ats_from_url(company.get("careersUrl") or company.get("website") or "")
    if not detected:
        return "", ""
    detected_type = str(detected.get("type") or "").strip().lower()
    detected_id = str(detected.get("id") or "").strip()
    if detected_type not in SUPPORTED_DIRECT_ATS_TYPES or not detected_id:
        return "", ""
    return detected_type, detected_id


__all__ = [
    "fetch_greenhouse_jobs",
    "fetch_lever_jobs",
    "fetch_smartrecruiters_jobs",
    "fetch_supported_ats_jobs",
    "partition_supported_companies",
    "resolve_supported_company_ats",
]
