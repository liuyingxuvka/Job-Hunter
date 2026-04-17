from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...ai.client import OpenAIResponsesClient
from ..analysis.service import JobAnalysisService, ResponseRequestClient
from ..companies.state import reconcile_company_pipeline_state_in_memory
from ..run_state import collect_resume_pending_jobs_from_job_lists
from .executor_common import _build_openai_client, _config_mapping, _now_iso, _remaining_seconds, _tail_lines
from .resume_pending_support import (
    _build_data_availability_note,
    _job_error_label,
    _job_progress_label,
    _load_candidate_profile_payload,
    _merge_jobs_for_resume,
    _merge_with_existing_job,
    _store_job,
)


@dataclass(frozen=True)
class PythonStageRunResult:
    success: bool
    exit_code: int
    message: str
    stdout_tail: str
    stderr_tail: str
    cancelled: bool = False
    payload: dict[str, Any] | None = None


class PythonStageExecutor:
    @classmethod
    def run_resume_pending_stage_for_runtime(
        cls,
        *,
        runtime_mirror,
        search_run_id: int,
        candidate_id: int,
        run_dir: Path,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[str], None] | None = None,
        client: ResponseRequestClient | None = None,
    ) -> PythonStageRunResult:
        if cancel_event is not None and cancel_event.is_set():
            return PythonStageRunResult(
                success=False,
                exit_code=-2,
                message="Python resume stage cancelled before start.",
                stdout_tail="",
                stderr_tail="",
                cancelled=True,
            )
        pending_jobs = runtime_mirror.load_run_bucket_jobs(
            search_run_id=search_run_id,
            job_bucket="resume_pending",
        )
        if not pending_jobs:
            return PythonStageRunResult(
                success=True,
                exit_code=0,
                message="Python resume stage skipped because no unfinished jobs remain.",
                stdout_tail="No pending jobs.",
                stderr_tail="",
            )

        deadline = time.monotonic() + max(1, int(timeout_seconds)) if timeout_seconds else None
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        try:
            client_instance = client or _build_openai_client(
                env=env,
                timeout_seconds=_remaining_seconds(deadline),
            )
        except Exception as exc:
            return PythonStageRunResult(
                success=False,
                exit_code=-1,
                message=f"Failed to initialize Python resume stage client: {exc}",
                stdout_tail="",
                stderr_tail=str(exc),
            )

        existing_jobs = runtime_mirror.load_run_bucket_jobs(
            search_run_id=search_run_id,
            job_bucket="all",
        )
        recommended_jobs = runtime_mirror.load_run_bucket_jobs(
            search_run_id=search_run_id,
            job_bucket="recommended",
        )
        working_jobs = _merge_jobs_for_resume(existing_jobs, pending_jobs)
        candidate_profile = _load_candidate_profile_payload(config, run_dir)
        data_availability_note = _build_data_availability_note(candidate_profile)
        analysis_config = _config_mapping(config, "analysis")
        post_verify_enabled = bool(analysis_config.get("postVerifyEnabled"))
        post_verify_cap = max(0, int(analysis_config.get("postVerifyMaxJobsPerRun") or 0))
        post_verify_count = 0
        processed_count = 0
        total = len(pending_jobs)

        def persist_outputs() -> int:
            all_jobs = list(working_jobs.values())
            runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=all_jobs,
            )
            remaining_pending = collect_resume_pending_jobs_from_job_lists(
                all_jobs,
                recommended_jobs,
            )
            runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="resume_pending",
                jobs=remaining_pending,
            )
            companies = runtime_mirror.load_candidate_company_pool(
                candidate_id=candidate_id,
            )
            if companies:
                reconcile_company_pipeline_state_in_memory(
                    companies=companies,
                    jobs=all_jobs,
                    config=config,
                )
                runtime_mirror.replace_candidate_company_pool(
                    candidate_id=candidate_id,
                    companies=companies,
                )
            return len(remaining_pending)

        for index, pending_job in enumerate(pending_jobs, start=1):
            if cancel_event is not None and cancel_event.is_set():
                persist_outputs()
                return PythonStageRunResult(
                    success=False,
                    exit_code=-2,
                    message="Python resume stage cancelled while analyzing unfinished jobs.",
                    stdout_tail=_tail_lines(stdout_lines),
                    stderr_tail=_tail_lines(stderr_lines),
                    cancelled=True,
                )

            remaining = _remaining_seconds(deadline)
            if remaining <= 0:
                persist_outputs()
                message = "Python resume stage timed out before all unfinished jobs were analyzed."
                stderr_lines.append(message)
                return PythonStageRunResult(
                    success=False,
                    exit_code=124,
                    message=message,
                    stdout_tail=_tail_lines(stdout_lines),
                    stderr_tail=_tail_lines(stderr_lines),
                )

            if isinstance(client_instance, OpenAIResponsesClient):
                client_instance.timeout_seconds = max(1, remaining)

            merged_job = _merge_with_existing_job(working_jobs, pending_job)
            progress_line = _job_progress_label(index, total, merged_job)
            stdout_lines.append(progress_line)
            if progress_callback is not None:
                progress_callback(progress_line)

            try:
                analysis = JobAnalysisService.score_job_fit(
                    client_instance,
                    config=config,
                    candidate_profile=candidate_profile,
                    job=merged_job,
                    data_availability_note=data_availability_note,
                )
                role_binding = JobAnalysisService.evaluate_target_roles_for_job(
                    client_instance,
                    config=config,
                    candidate_profile=candidate_profile,
                    job=merged_job,
                    analysis=analysis,
                )
                analysis_payload = JobAnalysisService.prepare_analysis_for_storage(
                    analysis,
                    role_binding,
                    config=config,
                )
                analysis_payload.pop("postVerify", None)
                analysis_payload["updatedAt"] = _now_iso()

                updated_job = dict(merged_job)
                updated_job.pop("postVerify", None)
                if (
                    post_verify_enabled
                    and analysis_payload.get("recommend") is True
                    and (post_verify_cap <= 0 or post_verify_count < post_verify_cap)
                ):
                    if isinstance(client_instance, OpenAIResponsesClient):
                        client_instance.timeout_seconds = max(1, _remaining_seconds(deadline))
                    post_verify = JobAnalysisService.post_verify_recommended_job(
                        client_instance,
                        config=config,
                        job=updated_job,
                    )
                    analysis_payload["postVerify"] = post_verify
                    updated_job["postVerify"] = post_verify
                    verified_location = str(post_verify.get("location") or "").strip()
                    if verified_location:
                        analysis_payload["location"] = verified_location
                        if not str(updated_job.get("location") or "").strip():
                            updated_job["location"] = verified_location
                    post_verify_count += 1

                updated_job["analysis"] = analysis_payload
                _store_job(working_jobs, updated_job)
                persist_outputs()
                processed_count += 1
            except Exception as exc:
                message = f"Python resume stage failed while analyzing {_job_error_label(merged_job)}: {exc}"
                stderr_lines.append(message)
                persist_outputs()
                return PythonStageRunResult(
                    success=False,
                    exit_code=-1,
                    message=message,
                    stdout_tail=_tail_lines(stdout_lines),
                    stderr_tail=_tail_lines(stderr_lines),
                )

        remaining_pending = persist_outputs()
        message = (
            f"Python resume stage completed. Analyzed {processed_count} unfinished job(s); "
            f"{remaining_pending} pending job(s) remain."
        )
        stdout_lines.append(message)
        return PythonStageRunResult(
            success=True,
            exit_code=0,
            message=message,
            stdout_tail=_tail_lines(stdout_lines),
            stderr_tail=_tail_lines(stderr_lines),
        )

    @classmethod
    def run_company_discovery_stage_for_runtime(
        cls,
        *,
        runtime_mirror,
        search_run_id: int,
        candidate_id: int,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        query_budget: int | None = None,
        max_new_companies: int | None = None,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[str], None] | None = None,
        client: ResponseRequestClient | None = None,
    ) -> PythonStageRunResult:
        if cancel_event is not None and cancel_event.is_set():
            return PythonStageRunResult(
                success=False,
                exit_code=-2,
                message="Python company discovery stage cancelled before start.",
                stdout_tail="",
                stderr_tail="",
                cancelled=True,
            )
        company_discovery = _config_mapping(config, "companyDiscovery")
        queries = [
            str(item or "").strip()
            for item in company_discovery.get("queries", [])
            if str(item or "").strip()
        ]
        if company_discovery.get("enableAutoDiscovery") is False or not queries:
            return PythonStageRunResult(
                success=True,
                exit_code=0,
                message="Python company discovery stage skipped because auto discovery is disabled or no queries are configured.",
                stdout_tail="",
                stderr_tail="",
            )
        deadline = time.monotonic() + max(1, int(timeout_seconds)) if timeout_seconds else None
        try:
            client_instance = client or _build_openai_client(
                env=env,
                timeout_seconds=_remaining_seconds(deadline),
            )
        except Exception as exc:
            return PythonStageRunResult(
                success=False,
                exit_code=-1,
                message=f"Failed to initialize Python company discovery client: {exc}",
                stdout_tail="",
                stderr_tail=str(exc),
            )
        if isinstance(client_instance, OpenAIResponsesClient):
            client_instance.timeout_seconds = max(1, _remaining_seconds(deadline))
        from .executor_company_stages import run_company_discovery_stage_db

        return run_company_discovery_stage_db(
            runtime_mirror=runtime_mirror,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            config=config,
            client_instance=client_instance,
            query_budget=query_budget,
            max_new_companies=max_new_companies,
            progress_callback=progress_callback,
        )

    @classmethod
    def run_company_selection_stage_for_runtime(
        cls,
        *,
        runtime_mirror,
        candidate_id: int,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        max_companies: int | None = None,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> PythonStageRunResult:
        if cancel_event is not None and cancel_event.is_set():
            return PythonStageRunResult(
                success=False,
                exit_code=-2,
                message="Python company selection stage cancelled before start.",
                stdout_tail="",
                stderr_tail="",
                cancelled=True,
            )
        from .executor_company_stages import run_company_selection_stage_db

        return run_company_selection_stage_db(
            runtime_mirror=runtime_mirror,
            candidate_id=candidate_id,
            config=config,
            max_companies=max_companies,
            progress_callback=progress_callback,
        )

    @classmethod
    def run_company_sources_stage_for_runtime(
        cls,
        *,
        runtime_mirror,
        search_run_id: int,
        candidate_id: int,
        config: dict[str, Any],
        selected_companies: list[dict[str, Any]] | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[str], None] | None = None,
        client: ResponseRequestClient | None = None,
    ) -> PythonStageRunResult:
        if cancel_event is not None and cancel_event.is_set():
            return PythonStageRunResult(
                success=False,
                exit_code=-2,
                message="Python company sources stage cancelled before start.",
                stdout_tail="",
                stderr_tail="",
                cancelled=True,
            )
        client_instance: ResponseRequestClient | None = client
        if client_instance is None:
            try:
                client_instance = _build_openai_client(
                    env=env,
                    timeout_seconds=max(1, int(timeout_seconds or 90)),
                )
            except Exception:
                client_instance = None
        from .executor_company_stages import run_company_sources_stage_db

        return run_company_sources_stage_db(
            runtime_mirror=runtime_mirror,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            config=config,
            selected_companies=selected_companies,
            client_instance=client_instance,
            timeout_seconds=timeout_seconds,
            progress_callback=progress_callback,
        )


__all__ = [
    "PythonStageExecutor",
    "PythonStageRunResult",
]
