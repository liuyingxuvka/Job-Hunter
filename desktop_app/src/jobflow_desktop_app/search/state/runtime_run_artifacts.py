from __future__ import annotations

from typing import Any

from .runtime_job_sync import write_runtime_job_buckets


class SearchRunArtifactsStore:
    def __init__(
        self,
        *,
        search_runs,
        candidate_companies,
        jobs,
        analyses,
        run_jobs,
    ) -> None:
        self.search_runs = search_runs
        self.candidate_companies = candidate_companies
        self.jobs = jobs
        self.analyses = analyses
        self.run_jobs = run_jobs

    def load_latest_bucket_jobs(
        self,
        *,
        candidate_id: int,
        job_bucket: str,
    ) -> list[dict[str, Any]]:
        # Candidate runtime workspaces persist across runs, so "latest bucket jobs"
        # must follow the newest search_run row rather than the mutable run_dir alone.
        latest_run = self.search_runs.latest_for_candidate(candidate_id)
        if latest_run is None:
            return []
        return self.run_jobs.load_bucket_jobs(
            search_run_id=latest_run.search_run_id,
            job_bucket=job_bucket,
        )

    def load_run_bucket_jobs(
        self,
        *,
        search_run_id: int,
        job_bucket: str,
    ) -> list[dict[str, Any]]:
        return self.run_jobs.load_bucket_jobs(
            search_run_id=search_run_id,
            job_bucket=job_bucket,
        )

    def replace_bucket_jobs(
        self,
        *,
        search_run_id: int,
        candidate_id: int,
        job_bucket: str,
        jobs: list[dict[str, Any]],
        refresh_counts: bool = True,
    ) -> None:
        buckets = {
            str(job_bucket).strip() or "all": [
                dict(item) for item in jobs if isinstance(item, dict)
            ]
        }
        write_runtime_job_buckets(
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            buckets=buckets,
            jobs_repo=self.jobs,
            analyses_repo=self.analyses,
            run_jobs_repo=self.run_jobs,
        )
        if refresh_counts:
            self.refresh_counts(search_run_id=search_run_id)

    def commit_company_sources_round(
        self,
        *,
        search_run_id: int,
        candidate_id: int,
        all_jobs: list[dict[str, Any]],
        found_jobs: list[dict[str, Any]],
        candidate_companies: list[dict[str, Any]],
    ) -> None:
        buckets = {
            "all": [dict(item) for item in all_jobs if isinstance(item, dict)],
            "found": [dict(item) for item in found_jobs if isinstance(item, dict)],
        }
        write_runtime_job_buckets(
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            buckets=buckets,
            jobs_repo=self.jobs,
            analyses_repo=self.analyses,
            run_jobs_repo=self.run_jobs,
        )
        self.candidate_companies.replace_candidate_pool(
            candidate_id=candidate_id,
            companies=[dict(item) for item in candidate_companies if isinstance(item, dict)],
        )
        self.refresh_counts(search_run_id=search_run_id)

    def refresh_counts(self, *, search_run_id: int) -> None:
        counts = self.run_jobs.summarize_bucket_counts(search_run_id=search_run_id)
        self.search_runs.update_counts(
            search_run_id,
            jobs_found_count=counts.jobs_found_count,
            jobs_scored_count=counts.jobs_scored_count,
            jobs_recommended_count=counts.jobs_recommended_count,
        )


__all__ = ["SearchRunArtifactsStore"]
