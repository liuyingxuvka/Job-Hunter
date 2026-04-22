from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ...db.repositories.candidates import CandidateRecord
from ...db.repositories.profiles import SearchProfileRecord
from ...db.repositories.settings import OpenAISettings
from ...ai.role_recommendations import CandidateSemanticProfile
from . import job_search_runner_runtime_io
from ..run_state import (
    analysis_completed as state_analysis_completed,
    collect_resume_pending_jobs_from_job_lists as state_collect_resume_pending_jobs_from_job_lists,
    job_identity_key as state_job_identity_key,
    job_item_key as state_job_item_key,
    merge_job_item as state_merge_job_item,
    merge_resume_pending_job_lists as state_merge_resume_pending_job_lists,
    normalize_resume_pending_jobs as state_normalize_resume_pending_jobs,
)
from ..state.search_progress_state import SearchProgress, SearchStats
from ..state.runtime_db_mirror import build_search_runtime_mirror
from ..state.runtime_run_locator import candidate_id_from_run_dir, project_root_from_run_dir
from . import (
    job_search_runner_session,
)

@dataclass(frozen=True)
class SearchRunResult:
    success: bool
    exit_code: int
    message: str
    stdout_tail: str
    stderr_tail: str
    run_dir: Path
    cancelled: bool = False
    details: dict[str, object] | None = None


@dataclass(frozen=True)
class JobSearchResult:
    title: str
    company: str
    location: str
    url: str
    date_found: str
    match_score: int | None
    recommend: bool
    fit_level_cn: str
    fit_track: str
    adjacent_direction_cn: str
    overall_match_score: int | None = None
    bound_target_role_id: str = ""
    bound_target_role_profile_id: int | None = None
    bound_target_role_name_zh: str = ""
    bound_target_role_name_en: str = ""
    bound_target_role_display_name: str = ""
    bound_target_role_text: str = ""
    bound_target_role_score: int | None = None
    source_url: str = ""
    final_url: str = ""
    link_status: str = "source"


@dataclass(frozen=True)
class SearchStageRunResult:
    success: bool
    exit_code: int
    message: str
    stdout_tail: str
    stderr_tail: str
    cancelled: bool = False


class JobSearchRunner:
    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self.runtime_root = self._ensure_runtime_root(self.project_root / "runtime")
        self.runtime_mirror = build_search_runtime_mirror(self.project_root)

    @staticmethod
    def _ensure_runtime_root(runtime_dir: Path) -> Path:
        search_runs_dir = runtime_dir / "search_runs"
        search_runs_dir.mkdir(parents=True, exist_ok=True)
        return search_runs_dir

    def run_search(
        self,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        settings: OpenAISettings | None = None,
        api_base_url: str = "",
        max_companies: int = 20,
        timeout_seconds: int = 900,
        cancel_event: threading.Event | None = None,
    ) -> SearchRunResult:
        return job_search_runner_session.run_search(
            self,
            candidate=candidate,
            profiles=profiles,
            settings=settings,
            api_base_url=api_base_url,
            max_companies=max_companies,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
            result_factory=SearchRunResult,
            time_ns_fn=time.time_ns,
            time_monotonic_fn=time.monotonic,
        )

    def load_recommended_jobs(self, candidate_id: int) -> list[JobSearchResult]:
        return job_search_runner_runtime_io.load_recommended_jobs(
            self,
            candidate_id,
            job_result_factory=JobSearchResult,
        )

    def load_live_jobs(self, candidate_id: int) -> list[JobSearchResult]:
        return job_search_runner_runtime_io.load_live_jobs(
            self,
            candidate_id,
            job_result_factory=JobSearchResult,
        )

    def load_search_stats(self, candidate_id: int) -> SearchStats:
        return job_search_runner_runtime_io.load_search_stats(self, candidate_id)

    def load_search_progress(self, candidate_id: int) -> SearchProgress:
        return job_search_runner_runtime_io.load_search_progress(self, candidate_id)

    def _refresh_python_recommended_output_json(
        self,
        run_dir: Path,
        config: dict | None,
    ) -> int:
        return job_search_runner_runtime_io.refresh_python_recommended_output_json(
            self,
            run_dir,
            config,
        )

    @staticmethod
    def _job_item_key(item: dict) -> str:
        return state_job_item_key(item)

    @staticmethod
    def _job_identity_key(item: dict) -> str:
        return state_job_identity_key(item)

    @staticmethod
    def _merge_job_item(existing: dict, incoming: dict) -> dict:
        return state_merge_job_item(existing, incoming)

    @classmethod
    def _analysis_completed(cls, analysis: object) -> bool:
        return state_analysis_completed(analysis)

    @staticmethod
    def _search_run_context_for_run_dir(run_dir: Path):
        candidate_id = candidate_id_from_run_dir(run_dir)
        if candidate_id is None:
            return None, None, None
        project_root = project_root_from_run_dir(run_dir)
        if project_root is None:
            return candidate_id, None, None
        runtime_mirror = build_search_runtime_mirror(project_root)
        if runtime_mirror is None:
            return candidate_id, None, None
        return candidate_id, runtime_mirror, runtime_mirror.latest_run(candidate_id)

    @classmethod
    def _previous_search_run_snapshot(
        cls,
        run_dir: Path,
        *,
        current_run_id: int | None = None,
    ):
        candidate_id, runtime_mirror, latest_run = cls._search_run_context_for_run_dir(run_dir)
        if runtime_mirror is None or candidate_id is None:
            return None
        if current_run_id is None:
            return latest_run
        recent_runs = list(runtime_mirror.recent_runs(candidate_id, limit=2))
        if not recent_runs:
            return None
        if int(recent_runs[0].search_run_id) != int(current_run_id):
            return recent_runs[0]
        return recent_runs[1] if len(recent_runs) > 1 else None

    @classmethod
    def _merge_resume_pending_job_lists(
        cls,
        run_dir: Path,
        *job_lists: list[dict],
        include_found_details: bool = False,
    ) -> list[dict]:
        return state_merge_resume_pending_job_lists(
            run_dir,
            *job_lists,
            include_found_details=include_found_details,
        )

    @classmethod
    def _normalize_resume_pending_jobs(
        cls,
        jobs: list[dict],
        run_dir: Path,
        include_found_details: bool = False,
    ) -> list[dict]:
        return state_normalize_resume_pending_jobs(
            jobs,
            run_dir,
            include_found_details=include_found_details,
        )

    @classmethod
    def _load_resume_pending_jobs(
        cls,
        run_dir: Path,
        include_fallback: bool = True,
        *,
        current_run_id: int | None = None,
    ) -> list[dict]:
        candidate_id, runtime_mirror, latest_run = cls._search_run_context_for_run_dir(run_dir)
        if runtime_mirror is None or latest_run is None or candidate_id is None:
            return []
        source_run = latest_run
        pending_jobs = runtime_mirror.load_run_bucket_jobs(
            search_run_id=source_run.search_run_id,
            job_bucket="resume_pending",
        )
        if not pending_jobs and current_run_id is not None and int(source_run.search_run_id) == int(current_run_id):
            previous_run = cls._previous_search_run_snapshot(
                run_dir,
                current_run_id=current_run_id,
            )
            if previous_run is not None:
                source_run = previous_run
                pending_jobs = runtime_mirror.load_run_bucket_jobs(
                    search_run_id=source_run.search_run_id,
                    job_bucket="resume_pending",
                )
        if pending_jobs and current_run_id is not None and int(source_run.search_run_id) == int(current_run_id):
            all_jobs = runtime_mirror.load_run_bucket_jobs(
                search_run_id=source_run.search_run_id,
                job_bucket="all",
            )
            recommended_jobs = runtime_mirror.load_run_bucket_jobs(
                search_run_id=source_run.search_run_id,
                job_bucket="recommended",
            )
            found_jobs = runtime_mirror.load_run_bucket_jobs(
                search_run_id=source_run.search_run_id,
                job_bucket="found",
            )
            return state_merge_resume_pending_job_lists(
                run_dir,
                pending_jobs,
                found_jobs,
                all_jobs,
                recommended_jobs,
                current_run_id=source_run.search_run_id,
            )
        if pending_jobs or not include_fallback:
            return state_normalize_resume_pending_jobs(
                pending_jobs,
                run_dir,
                current_run_id=source_run.search_run_id,
            )
        all_jobs = runtime_mirror.load_run_bucket_jobs(
            search_run_id=source_run.search_run_id,
            job_bucket="all",
        )
        recommended_jobs = runtime_mirror.load_run_bucket_jobs(
            search_run_id=source_run.search_run_id,
            job_bucket="recommended",
        )
        found_jobs = runtime_mirror.load_run_bucket_jobs(
            search_run_id=source_run.search_run_id,
            job_bucket="found",
        )
        pending_jobs = state_collect_resume_pending_jobs_from_job_lists(
            all_jobs,
            recommended_jobs,
        )
        if pending_jobs:
            return state_normalize_resume_pending_jobs(
                pending_jobs,
                run_dir,
                current_run_id=source_run.search_run_id,
            )
        return state_merge_resume_pending_job_lists(
            run_dir,
            found_jobs,
            all_jobs,
            recommended_jobs,
            current_run_id=source_run.search_run_id,
        )

    @classmethod
    def _write_resume_pending_jobs(
        cls,
        run_dir: Path,
        include_found_fallback: bool = False,
        *,
        current_run_id: int | None = None,
    ) -> int:
        pending_jobs = cls._load_resume_pending_jobs(
            run_dir,
            include_fallback=include_found_fallback,
            current_run_id=current_run_id,
        )
        candidate_id, runtime_mirror, latest_run = cls._search_run_context_for_run_dir(run_dir)
        if runtime_mirror is not None and latest_run is not None and candidate_id is not None:
            runtime_mirror.replace_bucket_jobs(
                search_run_id=latest_run.search_run_id,
                candidate_id=candidate_id,
                job_bucket="resume_pending",
                jobs=pending_jobs,
            )
        return len(pending_jobs)

    @classmethod
    def _clear_resume_pending_jobs(cls, run_dir: Path) -> None:
        candidate_id, runtime_mirror, latest_run = cls._search_run_context_for_run_dir(run_dir)
        if runtime_mirror is None or latest_run is None or candidate_id is None:
            return
        runtime_mirror.replace_bucket_jobs(
            search_run_id=latest_run.search_run_id,
            candidate_id=candidate_id,
            job_bucket="resume_pending",
            jobs=[],
        )

    @classmethod
    def _refresh_resume_pending_jobs(cls, run_dir: Path, *, current_run_id: int | None = None) -> int:
        try:
            return cls._write_resume_pending_jobs(
                run_dir,
                include_found_fallback=True,
                current_run_id=current_run_id,
            )
        except Exception:
            return 0

    def _candidate_run_dir(self, candidate_id: int) -> Path:
        return job_search_runner_session.candidate_run_dir(self, candidate_id)

    def _create_search_run(
        self,
        *,
        candidate_id: int,
        run_dir: Path,
        status: str,
        current_stage: str,
        started_at: str,
    ) -> int | None:
        return job_search_runner_runtime_io.create_search_run(
            self,
            candidate_id=candidate_id,
            run_dir=run_dir,
            status=status,
            current_stage=current_stage,
            started_at=started_at,
        )

    @classmethod
    def _load_search_progress_from_run_dir(cls, run_dir: Path) -> SearchProgress:
        return job_search_runner_session.load_search_progress_from_run_dir(run_dir)

    def _write_search_progress(
        self,
        run_dir: Path,
        *,
        status: str,
        stage: str,
        message: str = "",
        last_event: str = "",
        started_at: str = "",
        search_run_id: int | None = None,
    ) -> None:
        job_search_runner_runtime_io.write_search_progress(
            self,
            run_dir,
            status=status,
            stage=stage,
            message=message,
            last_event=last_event,
            started_at=started_at,
            search_run_id=search_run_id,
        )

    def _sync_search_run_configs(
        self,
        search_run_id: int | None,
        *,
        runtime_config: dict | None = None,
    ) -> None:
        job_search_runner_runtime_io.sync_search_run_configs(
            self,
            search_run_id,
            runtime_config=runtime_config,
        )

    def _store_semantic_profile_snapshot(
        self,
        *,
        candidate_id: int,
        semantic_profile: CandidateSemanticProfile | None,
    ) -> None:
        job_search_runner_runtime_io.store_semantic_profile_snapshot(
            self,
            candidate_id=candidate_id,
            semantic_profile=semantic_profile,
        )

    def _error_result(
        self,
        candidate_id: int,
        message: str,
        stdout_tail: str = "",
        stderr_tail: str = "",
    ) -> SearchRunResult:
        return job_search_runner_runtime_io.error_result(
            self,
            candidate_id,
            message,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            result_factory=SearchRunResult,
        )

    @staticmethod
    def _tail(text: str, max_lines: int = 40, max_chars: int = 4000) -> str:
        return job_search_runner_runtime_io.tail(text, max_lines=max_lines, max_chars=max_chars)

__all__ = [
    "JobSearchResult",
    "JobSearchRunner",
    "SearchRunResult",
    "SearchStageRunResult",
]

