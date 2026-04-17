from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .final_output import build_job_composite_key, canonical_job_url, normalize_job_url
from .manual_tracking import MANUAL_TRACKING_KEYS, has_manual_tracking, merge_manual_fields

def manual_tracking_aliases_for_job(job: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(job, Mapping):
        return []
    aliases: list[str] = []
    for candidate in (
        normalize_job_url(job.get("outputUrl") or ""),
        canonical_job_url(job),
        normalize_job_url(job.get("url") or ""),
        normalize_job_url(job.get("canonicalUrl") or ""),
    ):
        if candidate and candidate not in aliases:
            aliases.append(candidate)
    composite = build_job_composite_key(job)
    if composite and composite not in aliases:
        aliases.append(composite)
    return aliases


def extract_manual_fields_from_job(job: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(job, Mapping):
        return {key: "" for key in MANUAL_TRACKING_KEYS}
    return {
        key: str(job.get(key) or "").strip()
        for key in MANUAL_TRACKING_KEYS
    }


def resolve_manual_fields_for_job(
    job: Mapping[str, Any] | None,
    manual_by_alias: Mapping[str, Mapping[str, object]] | None,
) -> dict[str, str]:
    resolved = extract_manual_fields_from_job(job)
    if not isinstance(manual_by_alias, Mapping):
        return resolved
    for alias in manual_tracking_aliases_for_job(job):
        raw_row = manual_by_alias.get(alias)
        if not isinstance(raw_row, Mapping):
            continue
        for key in MANUAL_TRACKING_KEYS:
            value = str(raw_row.get(key) or "").strip()
            if value:
                resolved[key] = value
    return resolved


def overlay_manual_fields_onto_jobs(
    jobs: list[Mapping[str, Any]] | None,
    manual_by_alias: Mapping[str, Mapping[str, object]] | None,
) -> list[dict[str, Any]]:
    if not isinstance(jobs, list):
        return []
    overlaid: list[dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, Mapping):
            continue
        merged = dict(item)
        resolved = resolve_manual_fields_for_job(merged, manual_by_alias)
        for key, value in resolved.items():
            if value:
                merged[key] = value
        overlaid.append(merged)
    return overlaid


def collect_manual_fields_from_jobs(
    jobs: list[Mapping[str, Any]] | None,
    existing_manual_by_alias: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, dict[str, str]]:
    collected = merge_manual_fields(existing_manual_by_alias)
    if not isinstance(jobs, list):
        return collected
    for item in jobs:
        if not isinstance(item, Mapping):
            continue
        manual = extract_manual_fields_from_job(item)
        if not has_manual_tracking(manual):
            continue
        for alias in manual_tracking_aliases_for_job(item):
            previous = collected.get(alias, {})
            collected[alias] = {
                key: str(manual.get(key) or previous.get(key) or "").strip()
                for key in MANUAL_TRACKING_KEYS
            }
    return collected


__all__ = [
    "collect_manual_fields_from_jobs",
    "extract_manual_fields_from_job",
    "manual_tracking_aliases_for_job",
    "overlay_manual_fields_onto_jobs",
    "resolve_manual_fields_for_job",
]
