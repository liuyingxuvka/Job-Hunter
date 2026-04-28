from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import runtime_config_builder
from .stage_logging import stage_logger_for
from ..companies.state import reconcile_company_pipeline_state_in_memory
from ..stages.executor import PythonStageExecutor, PythonStageRunResult


@dataclass
class SearchSessionRuntime:
    runner: Any
    candidate_id: int
    candidate: Any
    profiles: list[Any]
    run_dir: Path
    base_config: dict
    resume_config: dict
    current_main_runtime_config: dict
    semantic_profile: Any
    model_override: str
    env: dict[str, str]
    cancel_event: threading.Event | None
    write_progress: Callable[..., None]
    progress_state: dict[str, str]
    max_companies: int
    effective_max_companies: int
    query_rotation_seed: int
    search_session_deadline: float
    candidate_search_signals: Any | None = None
    candidate_context: Any | None = None
    search_run_id: int | None = None
    stage_logger: Any | None = None


@dataclass(frozen=True)
class SearchSessionOutcome:
    success: bool
    exit_code: int
    message: str
    stdout_tail: str
    stderr_tail: str
    cancelled: bool = False
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class _StageResult:
    success: bool
    exit_code: int
    message: str
    stdout_tail: str
    stderr_tail: str
    cancelled: bool = False


def _set_stage(runtime: SearchSessionRuntime, stage: str) -> None:
    runtime.progress_state["current_stage"] = stage


def _sync_runtime_configs(
    runtime: SearchSessionRuntime,
    *,
    runtime_config: dict | None = None,
) -> None:
    sync_fn = getattr(runtime.runner, "_sync_search_run_configs", None)
    if not callable(sync_fn):
        return
    sync_fn(
        runtime.search_run_id,
        runtime_config=runtime_config,
    )


def _log_result_stage(
    runtime: SearchSessionRuntime,
    stage_name: str,
    message: str,
    callback: Callable[[], PythonStageRunResult | None],
    *,
    round_number: int = 0,
    counts: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> PythonStageRunResult | None:
    logger = stage_logger_for(runtime)
    token = logger.start(
        stage_name,
        round_number=round_number,
        message=message,
        counts=counts,
        metadata=metadata,
    )
    try:
        result = callback()
    except Exception as exc:
        logger.finish(
            token,
            status="hard_failed",
            exit_code=-1,
            message=f"{stage_name} raised an unexpected exception.",
            error_summary=str(exc),
            counts=counts,
            metadata=metadata,
        )
        raise
    return logger.finish_result(
        token,
        result,
        counts=counts,
        metadata=metadata,
    )


def _mark_stage_log_status(
    runtime: SearchSessionRuntime,
    stage_result: Any,
    *,
    status: str,
    message: str = "",
    error_summary: str = "",
) -> None:
    stage_logger_for(runtime).mark_status(
        stage_result,
        status=status,
        message=message,
        error_summary=error_summary,
    )


def _write_main_runtime_config(runtime: SearchSessionRuntime, rotation_seed: int) -> dict:
    logger = stage_logger_for(runtime)
    token = logger.start(
        "runtime_config",
        message="Preparing main runtime config.",
        metadata={"rotationSeed": rotation_seed},
    )
    candidate_context = runtime.candidate_context
    try:
        if isinstance(candidate_context, runtime_config_builder.RuntimeCandidateConfigContext):
            candidate_context = runtime_config_builder.refresh_runtime_candidate_context(
                candidate=runtime.candidate,
                profiles=runtime.profiles,
                semantic_profile=runtime.semantic_profile,
                candidate_context=candidate_context,
                signals=runtime.candidate_search_signals,
            )
            runtime.candidate_context = candidate_context
        runtime_config = runtime_config_builder.build_runtime_config(
            getattr(runtime.runner, "runtime_mirror", None),
            base_config=runtime.base_config,
            candidate=runtime.candidate,
            profiles=runtime.profiles,
            run_dir=runtime.run_dir,
            query_rotation_seed=rotation_seed,
            semantic_profile=runtime.semantic_profile,
            model_override=runtime.model_override,
            pipeline_stage="main",
            signals=runtime.candidate_search_signals,
            candidate_context=candidate_context,
        )
        _sync_runtime_configs(
            runtime,
            runtime_config=runtime_config,
        )
        runtime.effective_max_companies = runtime_config_builder.resolve_effective_max_companies(
            requested_max_companies=runtime.max_companies,
            runtime_config=runtime_config,
        )
        runtime.current_main_runtime_config = runtime_config
    except Exception as exc:
        logger.finish(
            token,
            status="hard_failed",
            exit_code=1,
            message="Runtime config could not be prepared.",
            error_summary=str(exc),
            metadata={"rotationSeed": rotation_seed},
        )
        raise
    logger.finish(
        token,
        status="success",
        exit_code=0,
        message="Runtime config prepared.",
        counts={
            "targetRoles": len(runtime_config.get("candidate", {}).get("targetRoles", []))
            if isinstance(runtime_config.get("candidate"), dict)
            else 0,
            "maxCompanies": runtime.effective_max_companies,
        },
        metadata={"rotationSeed": rotation_seed},
    )
    return runtime_config


def _remaining_search_session_seconds(runtime: SearchSessionRuntime) -> int:
    return max(0, int(runtime.search_session_deadline - time.monotonic()))


def _refresh_resume_pending_jobs(runtime: SearchSessionRuntime) -> int:
    count = runtime.runner._write_resume_pending_jobs(
        runtime.run_dir,
        include_found_fallback=True,
        current_run_id=runtime.search_run_id,
    )
    runtime_mirror = getattr(runtime.runner, "runtime_mirror", None)
    if runtime_mirror is not None and runtime.search_run_id is not None:
        try:
            companies = runtime_mirror.load_candidate_company_pool(
                candidate_id=runtime.candidate_id,
            )
            if companies:
                all_jobs = runtime_mirror.load_run_bucket_jobs(
                    search_run_id=runtime.search_run_id,
                    job_bucket="all",
                )
                reconciliation = reconcile_company_pipeline_state_in_memory(
                    companies=companies,
                    jobs=all_jobs,
                    config=runtime.current_main_runtime_config,
                )
                if bool(reconciliation.get("changed")):
                    runtime_mirror.replace_candidate_company_pool(
                        candidate_id=runtime.candidate_id,
                        companies=companies,
                    )
        except Exception:
            pass
    return count


def _refresh_python_recommended_outputs(
    runtime: SearchSessionRuntime,
    config_override: dict | None = None,
) -> int:
    logger = stage_logger_for(runtime)
    token = logger.start(
        "recommended_output_refresh",
        message="Refreshing recommended output.",
    )
    try:
        active_config = config_override or runtime.current_main_runtime_config
        count = runtime.runner._refresh_python_recommended_output_json(
            runtime.run_dir,
            active_config,
            search_run_id=runtime.search_run_id,
        )
    except Exception as exc:
        logger.finish(
            token,
            status="hard_failed",
            exit_code=1,
            message="Recommended output refresh failed.",
            error_summary=str(exc),
        )
        raise
    logger.finish(
        token,
        status="success",
        exit_code=0,
        message="Recommended output refreshed.",
        counts={"recommendedOutputJobs": count},
    )
    return count


def _run_resume_stage(
    runtime: SearchSessionRuntime,
    message: str,
    start_event: str,
    *,
    stage_name: str = "resume",
    round_number: int = 0,
) -> PythonStageRunResult | None:
    if runtime.search_run_id is None or runtime.runner.runtime_mirror is None:
        return None
    timeout = _remaining_search_session_seconds(runtime)
    if timeout <= 0:
        return None
    _set_stage(runtime, stage_name)
    runtime.write_progress(
        status="running",
        message=message,
        last_event=start_event,
    )
    return _log_result_stage(
        runtime,
        stage_name,
        message,
        lambda: PythonStageExecutor.run_resume_pending_stage_for_runtime(
            runtime_mirror=runtime.runner.runtime_mirror,
            search_run_id=runtime.search_run_id,
            candidate_id=runtime.candidate_id,
            run_dir=runtime.run_dir,
            config=runtime.resume_config,
            env=runtime.env,
            timeout_seconds=timeout,
            cancel_event=runtime.cancel_event,
            progress_callback=lambda line: runtime.write_progress(
                status="running",
                message=message,
                last_event=line,
            ),
        ),
        round_number=round_number,
    )


def _run_company_discovery_stage(
    runtime: SearchSessionRuntime,
    message: str,
    start_event: str,
    *,
    round_number: int = 0,
) -> PythonStageRunResult | None:
    if runtime.search_run_id is None or runtime.runner.runtime_mirror is None:
        return None
    timeout = _remaining_search_session_seconds(runtime)
    if timeout <= 0:
        return None
    _set_stage(runtime, "company_discovery")
    runtime.write_progress(
        status="running",
        message=message,
        last_event=start_event,
    )
    return _log_result_stage(
        runtime,
        "company_discovery",
        message,
        lambda: PythonStageExecutor.run_company_discovery_stage_for_runtime(
            runtime_mirror=runtime.runner.runtime_mirror,
            search_run_id=runtime.search_run_id,
            candidate_id=runtime.candidate_id,
            config=runtime.current_main_runtime_config,
            env=runtime.env,
            timeout_seconds=timeout,
            cancel_event=runtime.cancel_event,
            progress_callback=lambda line: runtime.write_progress(
                status="running",
                message=message,
                last_event=line,
            ),
        ),
        round_number=round_number,
    )


def _run_direct_job_discovery_stage(
    runtime: SearchSessionRuntime,
    message: str,
    start_event: str,
    *,
    round_number: int = 0,
) -> PythonStageRunResult | None:
    if runtime.search_run_id is None or runtime.runner.runtime_mirror is None:
        return None
    timeout = _remaining_search_session_seconds(runtime)
    if timeout <= 0:
        return None
    _set_stage(runtime, "direct_job_discovery")
    runtime.write_progress(
        status="running",
        message=message,
        last_event=start_event,
    )
    return _log_result_stage(
        runtime,
        "direct_job_discovery",
        message,
        lambda: PythonStageExecutor.run_direct_job_discovery_stage_for_runtime(
            runtime_mirror=runtime.runner.runtime_mirror,
            search_run_id=runtime.search_run_id,
            candidate_id=runtime.candidate_id,
            run_dir=runtime.run_dir,
            config=runtime.current_main_runtime_config,
            env=runtime.env,
            timeout_seconds=timeout,
            cancel_event=runtime.cancel_event,
            progress_callback=lambda line: runtime.write_progress(
                status="running",
                message=message,
                last_event=line,
            ),
        ),
        round_number=round_number,
    )


def _run_company_selection_stage(
    runtime: SearchSessionRuntime,
    message: str,
    start_event: str,
    *,
    round_number: int = 0,
) -> PythonStageRunResult | None:
    if runtime.runner.runtime_mirror is None:
        return None
    timeout = _remaining_search_session_seconds(runtime)
    if timeout <= 0:
        return None
    _set_stage(runtime, "company_selection")
    runtime.write_progress(
        status="running",
        message=message,
        last_event=start_event,
    )
    return _log_result_stage(
        runtime,
        "company_selection",
        message,
        lambda: PythonStageExecutor.run_company_selection_stage_for_runtime(
            runtime_mirror=runtime.runner.runtime_mirror,
            search_run_id=runtime.search_run_id,
            candidate_id=runtime.candidate_id,
            config=runtime.current_main_runtime_config,
            env=runtime.env,
            timeout_seconds=timeout,
            max_companies=runtime.effective_max_companies,
            cancel_event=runtime.cancel_event,
            progress_callback=lambda line: runtime.write_progress(
                status="running",
                message=message,
                last_event=line,
            ),
        ),
        round_number=round_number,
    )


def _run_company_sources_stage(
    runtime: SearchSessionRuntime,
    message: str,
    start_event: str,
    *,
    selected_companies: list[dict[str, Any]] | None = None,
    round_number: int = 0,
) -> PythonStageRunResult | None:
    if runtime.search_run_id is None or runtime.runner.runtime_mirror is None:
        return None
    timeout = _remaining_search_session_seconds(runtime)
    if timeout <= 0:
        return None
    _set_stage(runtime, "company_sources")
    runtime.write_progress(
        status="running",
        message=message,
        last_event=start_event,
    )
    return _log_result_stage(
        runtime,
        "company_sources",
        message,
        lambda: PythonStageExecutor.run_company_sources_stage_for_runtime(
            runtime_mirror=runtime.runner.runtime_mirror,
            search_run_id=runtime.search_run_id,
            candidate_id=runtime.candidate_id,
            config=runtime.current_main_runtime_config,
            selected_companies=selected_companies,
            env=runtime.env,
            timeout_seconds=timeout,
            cancel_event=runtime.cancel_event,
            progress_callback=lambda line: runtime.write_progress(
                status="running",
                message=message,
                last_event=line,
            ),
        ),
        round_number=round_number,
        counts={"inputSelectedCompanies": len(selected_companies or [])},
    )


def _combined_tail(runtime: SearchSessionRuntime, labeled_chunks: list[tuple[str, str]]) -> str:
    return runtime.runner._tail(
        "\n\n".join(
            f"[{label}]\n{text}" for label, text in labeled_chunks if text
        )
    )


def _cancelled_outcome(
    runtime: SearchSessionRuntime,
    message: str,
    *,
    stdout_tail: str = "",
    stderr_tail: str = "",
) -> SearchSessionOutcome:
    detail = runtime.runner._tail(
        stderr_tail or stdout_tail or message,
        max_lines=8,
        max_chars=1200,
    )
    runtime.runner._refresh_resume_pending_jobs(
        runtime.run_dir,
        current_run_id=runtime.search_run_id,
    )
    runtime.write_progress(
        status="cancelled",
        stage="done",
        message=message,
        last_event=detail,
    )
    return SearchSessionOutcome(
        success=False,
        exit_code=-2,
        message=message,
        stdout_tail=runtime.runner._tail(stdout_tail),
        stderr_tail=runtime.runner._tail(stderr_tail),
        cancelled=True,
    )


__all__ = [
    "SearchSessionOutcome",
    "SearchSessionRuntime",
    "_StageResult",
    "_cancelled_outcome",
    "_combined_tail",
    "_mark_stage_log_status",
    "_run_direct_job_discovery_stage",
    "_refresh_python_recommended_outputs",
    "_refresh_resume_pending_jobs",
    "_remaining_search_session_seconds",
    "_run_company_discovery_stage",
    "_run_company_selection_stage",
    "_run_company_sources_stage",
    "_run_resume_stage",
    "_set_stage",
    "_write_main_runtime_config",
]
