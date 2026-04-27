from __future__ import annotations

from typing import Any

from .runtime_job_sync import write_runtime_job_pool


class SearchRunArtifactsStore:
    def __init__(
        self,
        *,
        search_runs,
        candidate_companies,
        jobs,
        analyses,
        candidate_jobs=None,
    ) -> None:
        self.search_runs = search_runs
        self.candidate_companies = candidate_companies
        self.jobs = jobs
        self.analyses = analyses
        self.candidate_jobs = candidate_jobs

    def load_latest_bucket_jobs(
        self,
        *,
        candidate_id: int,
        job_bucket: str,
    ) -> list[dict[str, Any]]:
        return self._load_candidate_pool_jobs(
            candidate_id=candidate_id,
            job_bucket=job_bucket,
        )

    def load_candidate_bucket_jobs_merged(
        self,
        *,
        candidate_id: int,
        job_bucket: str,
    ) -> list[dict[str, Any]]:
        return self._load_candidate_pool_jobs(
            candidate_id=candidate_id,
            job_bucket=job_bucket,
        )

    def load_run_bucket_jobs(
        self,
        *,
        search_run_id: int,
        job_bucket: str,
    ) -> list[dict[str, Any]]:
        run = self.search_runs.get(search_run_id)
        if run is None:
            return []
        return self._load_candidate_pool_jobs(
            candidate_id=run.candidate_id,
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
        job_items = [dict(item) for item in jobs if isinstance(item, dict)]
        write_runtime_job_pool(
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            job_lists=[job_items],
            jobs_repo=self.jobs,
            analyses_repo=self.analyses,
            candidate_jobs_repo=self.candidate_jobs,
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
        write_runtime_job_pool(
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            job_lists=[
                [dict(item) for item in all_jobs if isinstance(item, dict)],
                [dict(item) for item in found_jobs if isinstance(item, dict)],
            ],
            jobs_repo=self.jobs,
            analyses_repo=self.analyses,
            candidate_jobs_repo=self.candidate_jobs,
        )
        self.candidate_companies.replace_candidate_pool(
            candidate_id=candidate_id,
            companies=[dict(item) for item in candidate_companies if isinstance(item, dict)],
        )
        self.refresh_counts(search_run_id=search_run_id)

    def refresh_counts(self, *, search_run_id: int) -> None:
        run = self.search_runs.get(search_run_id)
        if run is None or self.candidate_jobs is None:
            return
        counts = self.candidate_jobs.summarize_candidate(run.candidate_id)
        self.search_runs.update_counts(
            search_run_id,
            jobs_found_count=counts.total_jobs,
            jobs_scored_count=counts.scored_jobs,
            jobs_recommended_count=counts.recommended_jobs,
        )

    def persist_job_display_i18n(
        self,
        *,
        candidate_id: int,
        updates: dict[str, dict[str, Any]],
    ) -> None:
        if self.candidate_jobs is not None:
            self.candidate_jobs.persist_display_i18n(
                candidate_id=candidate_id,
                updates=updates,
            )

    def mark_recommended_output_set(self, *, candidate_id: int, job_keys: set[str]) -> None:
        if self.candidate_jobs is not None:
            self.candidate_jobs.mark_recommended_output_set(
                candidate_id=candidate_id,
                job_keys=job_keys,
            )

    def _load_candidate_pool_jobs(
        self,
        *,
        candidate_id: int,
        job_bucket: str,
    ) -> list[dict[str, Any]]:
        if self.candidate_jobs is None:
            return []
        normalized_bucket = str(job_bucket or "").strip()
        if normalized_bucket == "recommended":
            return self.candidate_jobs.load_recommended_payloads_for_candidate(candidate_id)
        if normalized_bucket == "resume_pending":
            return self.candidate_jobs.load_pending_payloads_for_candidate(candidate_id)
        return self.candidate_jobs.load_job_payloads_for_candidate(candidate_id)


__all__ = ["SearchRunArtifactsStore"]
