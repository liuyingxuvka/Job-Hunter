from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .discovery import (
    build_company_identity_keys,
    merge_source_evidence,
    merge_unique_strings,
)
from ..state.work_unit_state import normalize_work_unit_state


def _merge_monotonic_bool(existing: object, incoming: object) -> bool:
    return bool(existing) or bool(incoming)


def _merge_monotonic_mapping(
    existing: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, value in dict(incoming or {}).items():
        if isinstance(value, bool):
            merged[key] = _merge_monotonic_bool(merged.get(key), value)
            continue
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge_monotonic_mapping(
                dict(merged.get(key) or {}),
                dict(value),
            )
            continue
        if value not in ("", None, [], {}):
            merged[key] = value
    return merged


def merge_company_runtime_state(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for deprecated_key in ("aiCompanyRankingPending", "aiCompanyRankingError", "aiCompanyFitReason"):
        merged.pop(deprecated_key, None)
    list_merge_keys = {
        "tags",
        "discoverySources",
        "knownJobUrls",
        "snapshotJobUrls",
    }
    for key, value in incoming.items():
        if key == "snapshotComplete":
            merged[key] = bool(value)
            continue
        if isinstance(value, bool):
            merged[key] = _merge_monotonic_bool(merged.get(key), value)
            continue
        if key in list_merge_keys and isinstance(value, list):
            merged[key] = merge_unique_strings(merged.get(key), value)
            continue
        if key == "sourceEvidence":
            merged[key] = merge_source_evidence(merged.get(key), value)
            continue
        if key == "jobLinkCoverage" and isinstance(value, Mapping):
            current = dict(merged.get(key) or {}) if isinstance(merged.get(key), Mapping) else {}
            merged_coverage = dict(current)
            if isinstance(value.get("recentSeenJobUrls"), list):
                merged_coverage["recentSeenJobUrls"] = merge_unique_strings(
                    current.get("recentSeenJobUrls"),
                    value.get("recentSeenJobUrls"),
                )
            for numeric_key in ("cursor", "lastPoolSize"):
                if value.get(numeric_key) is not None:
                    merged_coverage[numeric_key] = max(
                        int(current.get(numeric_key) or 0),
                        int(value.get(numeric_key) or 0),
                    )
            merged[key] = merged_coverage
            continue
        if key == "jobPageCoverage" and isinstance(value, Mapping):
            current = dict(merged.get(key) or {}) if isinstance(merged.get(key), Mapping) else {}
            current_cache = (
                dict(current.get("listingPageCache") or {})
                if isinstance(current.get("listingPageCache"), Mapping)
                else {}
            )
            incoming_cache = (
                dict(value.get("listingPageCache") or {})
                if isinstance(value.get("listingPageCache"), Mapping)
                else {}
            )
            merged_cache = dict(current_cache)
            for cache_key, cache_value in incoming_cache.items():
                if cache_value in ("", None, [], {}):
                    continue
                merged_cache[str(cache_key)] = dict(cache_value) if isinstance(cache_value, Mapping) else cache_value
            current_company_search_cache = (
                dict(current.get("companySearchCache") or {})
                if isinstance(current.get("companySearchCache"), Mapping)
                else {}
            )
            incoming_company_search_cache = (
                dict(value.get("companySearchCache") or {})
                if isinstance(value.get("companySearchCache"), Mapping)
                else {}
            )
            merged_company_search_cache = dict(current_company_search_cache)
            for cache_key, cache_value in incoming_company_search_cache.items():
                if cache_value in ("", None, [], {}):
                    continue
                merged_company_search_cache[str(cache_key)] = (
                    dict(cache_value) if isinstance(cache_value, Mapping) else cache_value
                )
            merged[key] = {
                "visitedListingUrls": merge_unique_strings(value.get("visitedListingUrls")),
                "pendingListingUrls": merge_unique_strings(value.get("pendingListingUrls")),
                "coverageComplete": bool(value.get("coverageComplete")),
            }
            if merged_cache:
                merged[key]["listingPageCache"] = merged_cache
            if merged_company_search_cache:
                merged[key]["companySearchCache"] = merged_company_search_cache
            continue
        if key == "sourceWorkState":
            normalized_state = normalize_work_unit_state(value)
            if normalized_state:
                merged[key] = normalized_state
            else:
                merged.pop(key, None)
            continue
        if key == "sourceDiagnostics" and isinstance(value, Mapping):
            diagnostics = dict(value)
            diagnostics.pop("aiRankingError", None)
            merged[key] = diagnostics
            continue
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge_monotonic_mapping(
                dict(merged.get(key) or {}),
                dict(value),
            )
            continue
        if value not in ("", None, [], {}):
            merged[key] = value
    diagnostics = merged.get("sourceDiagnostics")
    if isinstance(diagnostics, Mapping):
        normalized_diagnostics = dict(diagnostics)
        normalized_diagnostics.pop("aiRankingError", None)
        merged["sourceDiagnostics"] = normalized_diagnostics
    return merged


def merge_companies_into_master(
    master_companies: list[Mapping[str, Any]],
    incoming_companies: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    merged_master = [
        dict(item)
        for item in master_companies
        if isinstance(item, Mapping)
    ]
    normalized_incoming = [
        dict(item)
        for item in incoming_companies
        if isinstance(item, Mapping)
    ]
    if not normalized_incoming:
        return merged_master, 0
    key_to_index: dict[str, int] = {}
    for index, company in enumerate(merged_master):
        for key in build_company_identity_keys(company):
            key_to_index[key] = index
    changed = 0
    for company in normalized_incoming:
        keys = build_company_identity_keys(company)
        index = next((key_to_index[key] for key in keys if key in key_to_index), None)
        if index is None:
            merged_master.append(company)
            new_index = len(merged_master) - 1
            for key in keys:
                key_to_index[key] = new_index
            changed += 1
            continue
        merged = merge_company_runtime_state(merged_master[index], company)
        if merged != merged_master[index]:
            merged_master[index] = merged
            for key in build_company_identity_keys(merged):
                key_to_index[key] = index
            changed += 1
    return merged_master, changed


__all__ = [
    "merge_companies_into_master",
    "merge_company_runtime_state",
]
