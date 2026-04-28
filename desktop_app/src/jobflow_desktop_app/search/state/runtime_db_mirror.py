from __future__ import annotations

from pathlib import Path
from typing import Any

from ...db.bootstrap import initialize_database
from ...db.connection import Database
from ...db.repositories.pools import CandidateJobPoolRepository
from ...db.repositories.stage_logs import SearchStageLogRepository
from ...db.repositories.search_runtime import (
    CandidateCompanyRepository,
    CandidateSemanticProfileRepository,
    JobAnalysisRepository,
    JobRepository,
    SearchRunRepository,
)
from ...paths import _resolve_schema_path
from .runtime_candidate_state import SearchRuntimeCandidateStateStore
from .runtime_run_artifacts import SearchRunArtifactsStore
from .runtime_run_state import SearchRunStateStore


class SearchRuntimeMirror:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.search_runs = SearchRunRepository(database)
        self.candidate_companies = CandidateCompanyRepository(database)
        self.semantic_profiles = CandidateSemanticProfileRepository(database)
        self.jobs = JobRepository(database)
        self.analyses = JobAnalysisRepository(database)
        self.candidate_jobs = CandidateJobPoolRepository(database)
        self.stage_logs = SearchStageLogRepository(database)
        self.run_state = SearchRunStateStore(
            search_runs=self.search_runs,
        )
        self.candidate_state = SearchRuntimeCandidateStateStore(
            candidate_companies=self.candidate_companies,
            semantic_profiles=self.semantic_profiles,
        )
        self.artifacts = SearchRunArtifactsStore(
            search_runs=self.search_runs,
            candidate_companies=self.candidate_companies,
            jobs=self.jobs,
            analyses=self.analyses,
            candidate_jobs=self.candidate_jobs,
        )

    def create_run(
        self,
        *,
        candidate_id: int,
        run_dir: Path,
        status: str,
        current_stage: str,
        started_at: str,
    ) -> int:
        return self.run_state.create_run(
            candidate_id=candidate_id,
            run_dir=str(run_dir.resolve()),
            status=status,
            current_stage=current_stage,
            started_at=started_at,
        )

    def update_progress(
        self,
        search_run_id: int | None,
        *,
        status: str,
        stage: str,
        message: str = "",
        last_event: str = "",
        started_at: str = "",
    ) -> None:
        self.run_state.update_progress(
            search_run_id,
            status=status,
            stage=stage,
            message=message,
            last_event=last_event,
            started_at=started_at,
        )

    def update_configs(
        self,
        search_run_id: int | None,
        *,
        runtime_config: dict[str, Any] | None = None,
    ) -> None:
        self.run_state.update_configs(
            search_run_id,
            runtime_config=runtime_config,
        )

    def start_stage_log(
        self,
        *,
        search_run_id: int,
        candidate_id: int | None,
        round_number: int = 0,
        stage_name: str,
        message: str = "",
        counts: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        return self.stage_logs.start(
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            round_number=round_number,
            stage_name=stage_name,
            message=message,
            counts=counts,
            metadata=metadata,
        )

    def finish_stage_log(
        self,
        log_id: int,
        *,
        status: str,
        exit_code: int | None = None,
        message: str = "",
        error_summary: str = "",
        counts: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self.stage_logs.finish(
            log_id,
            status=status,
            exit_code=exit_code,
            message=message,
            error_summary=error_summary,
            counts=counts,
            metadata=metadata,
            duration_ms=duration_ms,
        )

    def update_stage_log_status(
        self,
        log_id: int,
        *,
        status: str,
        message: str = "",
        error_summary: str = "",
    ) -> None:
        self.stage_logs.update_status(
            log_id,
            status=status,
            message=message,
            error_summary=error_summary,
        )

    def list_stage_logs_for_run(self, search_run_id: int) -> list[Any]:
        return self.stage_logs.list_for_run(search_run_id)

    def store_semantic_profile(
        self,
        *,
        candidate_id: int,
        profile_payload: dict[str, Any] | None,
    ) -> None:
        self.candidate_state.store_semantic_profile(
            candidate_id=candidate_id,
            profile_payload=profile_payload,
        )

    def latest_run(self, candidate_id: int) -> Any | None:
        return self.run_state.latest_run(candidate_id)

    def recent_runs(self, candidate_id: int, *, limit: int = 5) -> list[Any]:
        return self.run_state.recent_runs(candidate_id, limit=limit)

    def all_runs(self, candidate_id: int) -> list[Any]:
        return self.run_state.all_runs(candidate_id)

    def load_latest_bucket_jobs(
        self,
        *,
        candidate_id: int,
        job_bucket: str,
    ) -> list[dict[str, Any]]:
        return self.artifacts.load_latest_bucket_jobs(
            candidate_id=candidate_id,
            job_bucket=job_bucket,
        )

    def load_candidate_bucket_jobs_merged(
        self,
        *,
        candidate_id: int,
        job_bucket: str,
    ) -> list[dict[str, Any]]:
        return self.artifacts.load_candidate_bucket_jobs_merged(
            candidate_id=candidate_id,
            job_bucket=job_bucket,
        )

    def load_latest_progress_payload(self, candidate_id: int) -> dict[str, Any] | None:
        return self.run_state.load_latest_progress_payload(candidate_id)

    def load_run_config(
        self,
        *,
        candidate_id: int,
    ) -> dict[str, Any]:
        return self.run_state.load_run_config(candidate_id=candidate_id)

    def count_candidate_company_pool(self, candidate_id: int) -> int:
        return self.candidate_state.count_candidate_company_pool(candidate_id)

    def load_candidate_company_pool(
        self,
        *,
        candidate_id: int,
    ) -> list[dict[str, Any]]:
        return self.candidate_state.load_candidate_company_pool(
            candidate_id=candidate_id,
        )

    def backfill_candidate_job_pool_from_legacy(self, candidate_id: int) -> Any:
        return self.candidate_jobs.backfill_candidate_from_legacy(candidate_id)

    def load_candidate_job_pool_payloads(self, *, candidate_id: int) -> list[dict[str, Any]]:
        jobs = self.candidate_jobs.load_job_payloads_for_candidate(candidate_id)
        if jobs:
            return jobs
        self.candidate_jobs.backfill_candidate_from_legacy(candidate_id)
        return self.candidate_jobs.load_job_payloads_for_candidate(candidate_id)

    def load_candidate_recommended_job_pool_payloads(self, *, candidate_id: int) -> list[dict[str, Any]]:
        jobs = self.candidate_jobs.load_recommended_payloads_for_candidate(candidate_id)
        if jobs:
            return jobs
        self.candidate_jobs.backfill_candidate_from_legacy(candidate_id)
        return self.candidate_jobs.load_recommended_payloads_for_candidate(candidate_id)

    def load_candidate_pending_job_pool_payloads(self, *, candidate_id: int) -> list[dict[str, Any]]:
        summary = self.candidate_jobs.summarize_candidate(candidate_id)
        if not summary.total_jobs:
            self.candidate_jobs.backfill_candidate_from_legacy(candidate_id)
        return self.candidate_jobs.load_pending_payloads_for_candidate(candidate_id)

    def summarize_candidate_job_pool(self, *, candidate_id: int) -> Any:
        summary = self.candidate_jobs.summarize_candidate(candidate_id)
        if summary.total_jobs:
            return summary
        self.candidate_jobs.backfill_candidate_from_legacy(candidate_id)
        return self.candidate_jobs.summarize_candidate(candidate_id)

    def replace_candidate_company_pool(
        self,
        *,
        candidate_id: int,
        companies: list[dict[str, Any]],
    ) -> None:
        self.candidate_state.replace_candidate_company_pool(
            candidate_id=candidate_id,
            companies=companies,
        )

    def load_run_bucket_jobs(
        self,
        *,
        search_run_id: int,
        job_bucket: str,
    ) -> list[dict[str, Any]]:
        return self.artifacts.load_run_bucket_jobs(
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
        self.artifacts.replace_bucket_jobs(
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            job_bucket=job_bucket,
            jobs=jobs,
            refresh_counts=refresh_counts,
        )

    def commit_company_sources_round(
        self,
        *,
        search_run_id: int,
        candidate_id: int,
        all_jobs: list[dict[str, Any]],
        found_jobs: list[dict[str, Any]],
        candidate_companies: list[dict[str, Any]],
    ) -> None:
        self.artifacts.commit_company_sources_round(
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            all_jobs=all_jobs,
            found_jobs=found_jobs,
            candidate_companies=candidate_companies,
        )

    def refresh_counts(self, *, search_run_id: int) -> None:
        self.artifacts.refresh_counts(search_run_id=search_run_id)

    def persist_job_display_i18n(
        self,
        *,
        candidate_id: int,
        updates: dict[str, dict[str, Any]],
    ) -> None:
        self.artifacts.persist_job_display_i18n(
            candidate_id=candidate_id,
            updates=updates,
        )

    def mark_recommended_output_set(self, *, candidate_id: int, job_keys: set[str]) -> None:
        self.artifacts.mark_recommended_output_set(
            candidate_id=candidate_id,
            job_keys=job_keys,
        )


def build_search_runtime_mirror(project_root: Path) -> SearchRuntimeMirror | None:
    db_path = Path(project_root) / "runtime" / "data" / "jobflow_desktop.db"
    if not db_path.exists():
        return None
    database = Database(db_path)
    schema_path = _resolve_schema_path(Path(project_root))
    if schema_path.exists():
        initialize_database(database, schema_path)
    return SearchRuntimeMirror(database)


__all__ = [
    "SearchRuntimeMirror",
    "build_search_runtime_mirror",
]
