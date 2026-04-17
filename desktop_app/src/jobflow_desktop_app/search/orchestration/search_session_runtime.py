from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import runtime_config_builder
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
    session_pass_timeout_seconds: int
    candidate_search_signals: Any | None = None
    candidate_context: Any | None = None
    search_run_id: int | None = None
    empty_rounds_before_end: int = 3


@dataclass(frozen=True)
class SearchSessionOutcome:
    success: bool
    exit_code: int
    message: str
    stdout_tail: str
    stderr_tail: str
    cancelled: bool = False


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
    resume_config: dict | None = None,
) -> None:
    sync_fn = getattr(runtime.runner, "_sync_search_run_configs", None)
    if not callable(sync_fn):
        return
    sync_fn(
        runtime.search_run_id,
        runtime_config=runtime_config,
        resume_config=resume_config,
    )


def _write_main_runtime_config(runtime: SearchSessionRuntime, rotation_seed: int) -> dict:
    candidate_context = runtime.candidate_context
    if isinstance(candidate_context, runtime_config_builder.RuntimeCandidateConfigContext):
        candidate_context = runtime_config_builder.refresh_runtime_candidate_context(
            getattr(runtime.runner, "runtime_mirror", None),
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
    return runtime_config


def _remaining_search_session_seconds(runtime: SearchSessionRuntime) -> int:
    return max(0, int(runtime.search_session_deadline - time.monotonic()))


def _stage_timeout_seconds(runtime: SearchSessionRuntime, default_timeout: int) -> int:
    return min(max(1, int(default_timeout)), _remaining_search_session_seconds(runtime))


def _refresh_resume_pending_jobs(runtime: SearchSessionRuntime) -> int:
    count = runtime.runner._write_resume_pending_jobs(
        runtime.run_dir,
        include_found_fallback=True,
    )
    return count


def _refresh_python_recommended_outputs(
    runtime: SearchSessionRuntime,
    config_override: dict | None = None,
) -> int:
    active_config = config_override or runtime.current_main_runtime_config
    count = runtime.runner._refresh_python_recommended_output_json(
        runtime.run_dir,
        active_config,
    )
    return count


def _run_resume_stage(
    runtime: SearchSessionRuntime,
    message: str,
    start_event: str,
    *,
    stage_name: str = "resume",
) -> PythonStageRunResult | None:
    if runtime.search_run_id is None or runtime.runner.runtime_mirror is None:
        return None
    timeout = _stage_timeout_seconds(runtime, runtime.session_pass_timeout_seconds)
    if timeout <= 0:
        return None
    _set_stage(runtime, stage_name)
    runtime.write_progress(
        status="running",
        message=message,
        last_event=start_event,
    )
    return PythonStageExecutor.run_resume_pending_stage_for_runtime(
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
    )


def _run_company_discovery_stage(
    runtime: SearchSessionRuntime,
    message: str,
    start_event: str,
    *,
    query_budget: int,
    max_new_companies: int,
) -> PythonStageRunResult | None:
    if runtime.search_run_id is None or runtime.runner.runtime_mirror is None:
        return None
    if query_budget <= 0 or max_new_companies <= 0:
        return None
    timeout = _stage_timeout_seconds(runtime, runtime.session_pass_timeout_seconds)
    if timeout <= 0:
        return None
    _set_stage(runtime, "company_discovery")
    runtime.write_progress(
        status="running",
        message=message,
        last_event=start_event,
    )
    return PythonStageExecutor.run_company_discovery_stage_for_runtime(
        runtime_mirror=runtime.runner.runtime_mirror,
        search_run_id=runtime.search_run_id,
        candidate_id=runtime.candidate_id,
        config=runtime.current_main_runtime_config,
        env=runtime.env,
        timeout_seconds=timeout,
        query_budget=query_budget,
        max_new_companies=max_new_companies,
        cancel_event=runtime.cancel_event,
        progress_callback=lambda line: runtime.write_progress(
            status="running",
            message=message,
            last_event=line,
        ),
    )


def _run_company_selection_stage(
    runtime: SearchSessionRuntime,
    message: str,
    start_event: str,
) -> PythonStageRunResult | None:
    if runtime.runner.runtime_mirror is None:
        return None
    timeout = _stage_timeout_seconds(runtime, runtime.session_pass_timeout_seconds)
    if timeout <= 0:
        return None
    _set_stage(runtime, "company_selection")
    runtime.write_progress(
        status="running",
        message=message,
        last_event=start_event,
    )
    return PythonStageExecutor.run_company_selection_stage_for_runtime(
        runtime_mirror=runtime.runner.runtime_mirror,
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
    )


def _run_company_sources_stage(
    runtime: SearchSessionRuntime,
    message: str,
    start_event: str,
    *,
    selected_companies: list[dict[str, Any]] | None = None,
) -> PythonStageRunResult | None:
    if runtime.search_run_id is None or runtime.runner.runtime_mirror is None:
        return None
    timeout = _stage_timeout_seconds(runtime, runtime.session_pass_timeout_seconds)
    if timeout <= 0:
        return None
    _set_stage(runtime, "company_sources")
    runtime.write_progress(
        status="running",
        message=message,
        last_event=start_event,
    )
    return PythonStageExecutor.run_company_sources_stage_for_runtime(
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
    runtime.runner._refresh_resume_pending_jobs(runtime.run_dir)
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
    "_refresh_python_recommended_outputs",
    "_refresh_resume_pending_jobs",
    "_remaining_search_session_seconds",
    "_run_company_discovery_stage",
    "_run_company_selection_stage",
    "_run_company_sources_stage",
    "_run_resume_stage",
    "_set_stage",
    "_stage_timeout_seconds",
    "_write_main_runtime_config",
]
