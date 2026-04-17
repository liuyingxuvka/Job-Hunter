from __future__ import annotations

from pathlib import Path
from typing import Any

from ...db.connection import Database
from ...db.repositories.search_runtime import (
    CandidateCompanyRepository,
    CandidateSemanticProfileRepository,
    JobAnalysisRepository,
    JobRepository,
    SearchRunJobRepository,
    SearchRunRepository,
)
from .runtime_candidate_state import SearchRuntimeCandidateStateStore
from .runtime_run_artifacts import SearchRunArtifactsStore
from .runtime_run_feedback import SearchRunFeedbackStore
from .runtime_run_state import SearchRunStateStore


class SearchRuntimeMirror:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.search_runs = SearchRunRepository(database)
        self.candidate_companies = CandidateCompanyRepository(database)
        self.semantic_profiles = CandidateSemanticProfileRepository(database)
        self.jobs = JobRepository(database)
        self.analyses = JobAnalysisRepository(database)
        self.run_jobs = SearchRunJobRepository(database)
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
            run_jobs=self.run_jobs,
        )
        self.run_feedback = SearchRunFeedbackStore(
            artifacts=self.artifacts,
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
        resume_config: dict[str, Any] | None = None,
    ) -> None:
        self.run_state.update_configs(
            search_run_id,
            runtime_config=runtime_config,
            resume_config=resume_config,
        )

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

    def load_latest_progress_payload(self, candidate_id: int) -> dict[str, Any] | None:
        return self.run_state.load_latest_progress_payload(candidate_id)

    def load_latest_run_feedback(
        self,
        *,
        candidate_id: int,
    ) -> dict[str, list[str]]:
        return self.run_feedback.load_latest_run_feedback(
            candidate_id=candidate_id,
        )

    def load_run_config(
        self,
        *,
        candidate_id: int,
        resume: bool = False,
    ) -> dict[str, Any]:
        return self.run_state.load_run_config(
            candidate_id=candidate_id,
            resume=resume,
        )

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


def build_search_runtime_mirror(project_root: Path) -> SearchRuntimeMirror | None:
    db_path = Path(project_root) / "runtime" / "data" / "jobflow_desktop.db"
    if not db_path.exists():
        return None
    return SearchRuntimeMirror(Database(db_path))


__all__ = [
    "SearchRuntimeMirror",
    "build_search_runtime_mirror",
]
