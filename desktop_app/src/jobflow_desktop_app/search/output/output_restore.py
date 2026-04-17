from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..analysis.scoring_contract import overall_score


Job = dict[str, Any]
Row = Mapping[str, Any]


def _analysis_score(job: Mapping[str, Any]) -> int:
    analysis = job.get("analysis")
    if isinstance(analysis, Mapping):
        score = overall_score(analysis)
        return score if score > 0 else -1
    return -1


def sort_jobs_for_append_merge(jobs: Iterable[Mapping[str, Any]]) -> list[Job]:
    normalized = [dict(job) for job in jobs if isinstance(job, Mapping)]
    normalized.sort(
        key=lambda job: (_analysis_score(job), str(job.get("dateFound") or "")),
        reverse=True,
    )
    return normalized


@dataclass
class AppendMergeResult:
    merged_by_key: dict[str, Job]
    pruned_recent_invalid_rows: int = 0

    @property
    def jobs(self) -> list[Job]:
        return list(self.merged_by_key.values())


def merge_recommended_jobs_append_mode(
    *,
    existing_rows: Iterable[Row],
    all_jobs_by_key: Mapping[str, Mapping[str, Any]],
    historical_jobs: Iterable[Mapping[str, Any]],
    new_jobs: Iterable[Mapping[str, Any]],
    tracker_now: str,
    tracker_today: str,
    row_to_job: Callable[[Row], Job | None],
    key_for_job: Callable[[Mapping[str, Any]], str],
    passes_unified_threshold: Callable[[Mapping[str, Any]], bool],
    passes_final_output_check: Callable[[Mapping[str, Any]], bool],
    has_manual_tracking: Callable[[Row], bool],
    prefers_candidate_over_existing: Callable[[Mapping[str, Any], Mapping[str, Any]], bool],
) -> AppendMergeResult:
    merged: dict[str, Job] = {}
    pruned_recent_invalid_rows = 0

    for row in existing_rows:
        job = row_to_job(row)
        if not job:
            continue
        key = key_for_job(job)
        if not key:
            continue
        current_date_found = str(job.get("dateFound") or row.get("dateFound") or "").strip()
        rich_job = dict(all_jobs_by_key.get(key) or job)
        if not passes_unified_threshold(rich_job):
            continue
        if (
            current_date_found == tracker_today
            and not has_manual_tracking(row)
            and not passes_final_output_check(rich_job)
        ):
            pruned_recent_invalid_rows += 1
            continue
        if not str(job.get("dateFound") or "").strip():
            job = {**job, "dateFound": tracker_now}
        merged[key] = job

    historical_sorted = sorted(
        [dict(job) for job in historical_jobs if isinstance(job, Mapping)],
        key=lambda job: str(job.get("dateFound") or ""),
    )
    for job in historical_sorted:
        key = key_for_job(job)
        if not key:
            continue
        existing_job = merged.get(key)
        candidate = {
            **job,
            "dateFound": (
                str(existing_job.get("dateFound") or "").strip()
                if existing_job
                else str(job.get("dateFound") or "").strip() or tracker_now
            ),
        }
        if not existing_job and not passes_final_output_check(candidate):
            continue
        if not existing_job:
            merged[key] = candidate
            continue
        if not passes_final_output_check(candidate):
            if not str(existing_job.get("dateFound") or "").strip():
                merged[key] = {**existing_job, "dateFound": tracker_now}
            continue
        if prefers_candidate_over_existing(existing_job, candidate):
            merged[key] = candidate
        elif not str(existing_job.get("dateFound") or "").strip():
            merged[key] = {**existing_job, "dateFound": tracker_now}

    for job in sort_jobs_for_append_merge(new_jobs):
        key = key_for_job(job)
        if not key:
            continue
        existing_job = merged.get(key)
        if not existing_job:
            merged[key] = {**job, "dateFound": tracker_now}
            continue
        candidate = {
            **job,
            "dateFound": str(existing_job.get("dateFound") or "").strip()
            or str(job.get("dateFound") or "").strip()
            or tracker_now,
        }
        if prefers_candidate_over_existing(existing_job, candidate):
            merged[key] = candidate
        elif not str(existing_job.get("dateFound") or "").strip():
            merged[key] = {**existing_job, "dateFound": tracker_now}

    return AppendMergeResult(
        merged_by_key=merged,
        pruned_recent_invalid_rows=pruned_recent_invalid_rows,
    )
