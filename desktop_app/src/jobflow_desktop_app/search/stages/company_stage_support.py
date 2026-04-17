from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..companies.pool_store import merge_companies_into_master
from ..companies.sources import build_found_job_records, merge_company_source_jobs
from ..companies.state import reconcile_company_pipeline_state_in_memory

if TYPE_CHECKING:
    from typing import Any

    from ..companies.sources import CompanySourcesFetchResult


@dataclass(frozen=True)
class CompanySourcesStageArtifacts:
    candidate_companies: list[dict[str, Any]]
    deferred_companies: list[dict[str, Any]]
    all_jobs: list[dict[str, Any]]
    found_jobs: list[dict[str, Any]]


def build_company_sources_stage_artifacts(
    *,
    master_companies: list[dict[str, Any]],
    existing_jobs: list[dict[str, Any]],
    fetch_result: "CompanySourcesFetchResult",
    config: dict[str, Any],
) -> CompanySourcesStageArtifacts:
    merged_companies, _ = merge_companies_into_master(
        master_companies,
        fetch_result.processed_companies,
    )
    deferred_companies = [
        dict(item)
        for item in fetch_result.remaining_companies
        if isinstance(item, dict)
    ]
    merged_companies, _ = merge_companies_into_master(
        merged_companies,
        deferred_companies,
    )
    merged_jobs = merge_company_source_jobs(existing_jobs, fetch_result.jobs)
    found_jobs = build_found_job_records(
        fetch_result.jobs,
        existing_jobs=merged_jobs,
        config=config,
    )
    reconcile_company_pipeline_state_in_memory(
        companies=merged_companies,
        jobs=merged_jobs,
        config=config,
    )
    return CompanySourcesStageArtifacts(
        candidate_companies=merged_companies,
        deferred_companies=deferred_companies,
        all_jobs=merged_jobs,
        found_jobs=found_jobs,
    )


__all__ = [
    "CompanySourcesStageArtifacts",
    "build_company_sources_stage_artifacts",
]
