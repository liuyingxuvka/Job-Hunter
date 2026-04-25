from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ...ai.role_recommendations import (
    OpenAIRoleRecommendationService,
    RoleRecommendationError,
)
from ...db.repositories.candidates import CandidateRecord
from ...db.repositories.profiles import SearchProfileRecord
from ...db.repositories.settings import OpenAISettings
from ..state.search_progress_state import (
    SearchProgress,
    load_search_progress_from_run_dir as state_load_search_progress_from_run_dir,
    write_search_progress as state_write_search_progress,
)
from .. import runtime_strategy
from . import (
    candidate_search_signals as candidate_search_signals_module,
    runtime_config_builder,
    search_session_orchestrator,
)


def candidate_run_dir(runner, candidate_id: int) -> Path:
    return runner.runtime_root / f"candidate_{candidate_id}"


def load_candidate_semantic_profile_for_run(
    runner,
    *,
    candidate: CandidateRecord,
    settings: OpenAISettings | None,
    api_base_url: str,
    run_dir: Path,
):
    service = OpenAIRoleRecommendationService()
    profile_settings = settings.for_quality_model() if settings is not None else OpenAISettings()
    try:
        return service.extract_candidate_semantic_profile(
            candidate=candidate,
            settings=profile_settings,
            api_base_url=api_base_url,
            cache_path=None,
        )
    except RoleRecommendationError:
        return None


def load_search_progress_from_run_dir(run_dir: Path) -> SearchProgress:
    return state_load_search_progress_from_run_dir(run_dir)


def write_search_progress(
    run_dir: Path,
    *,
    status: str,
    stage: str,
    message: str = "",
    last_event: str = "",
    started_at: str = "",
) -> None:
    state_write_search_progress(
        run_dir,
        status=status,
        stage=stage,
        message=message,
        last_event=last_event,
        started_at=started_at,
    )


def error_result(
    runner,
    candidate_id: int,
    message: str,
    stdout_tail: str = "",
    stderr_tail: str = "",
    *,
    result_factory,
):
    run_dir = runner._candidate_run_dir(candidate_id)
    return result_factory(
        success=False,
        exit_code=-1,
        message=message,
        stdout_tail=runner._tail(stdout_tail),
        stderr_tail=runner._tail(stderr_tail),
        run_dir=run_dir,
    )


def tail(text: str, max_lines: int = 40, max_chars: int = 4000) -> str:
    lines = str(text or "").splitlines()
    clipped = "\n".join(lines[-max_lines:])
    if len(clipped) <= max_chars:
        return clipped
    return clipped[-max_chars:]


def run_search(
    runner,
    candidate: CandidateRecord,
    profiles: list[SearchProfileRecord],
    settings: OpenAISettings | None = None,
    api_base_url: str = "",
    max_companies: int = 20,
    timeout_seconds: int = 900,
    cancel_event: threading.Event | None = None,
    *,
    result_factory,
    time_ns_fn: Callable[[], int],
    time_monotonic_fn: Callable[[], float],
):
    if candidate.candidate_id is None:
        raise ValueError("Candidate ID is required.")
    run_dir = runner._candidate_run_dir(candidate.candidate_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    progress_started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    query_rotation_seed = time_ns_fn()
    progress_state = {"current_stage": "preparing"}
    progress_lock = threading.Lock()
    search_run_id = runner._create_search_run(
        candidate_id=candidate.candidate_id,
        run_dir=run_dir,
        status="running",
        current_stage=progress_state["current_stage"],
        started_at=progress_started_at,
    )

    def write_progress(
        *,
        status: str,
        stage: str | None = None,
        message: str = "",
        last_event: str = "",
    ) -> None:
        with progress_lock:
            runner._write_search_progress(
                run_dir,
                status=status,
                stage=stage or progress_state["current_stage"],
                message=message,
                last_event=last_event,
                started_at=progress_started_at,
                search_run_id=search_run_id,
            )

    def error_result_for_run(message: str, stdout_tail: str = "", stderr_tail: str = ""):
        detail = runner._tail(stderr_tail or stdout_tail or message, max_lines=8, max_chars=1200)
        runner._refresh_resume_pending_jobs(run_dir, current_run_id=search_run_id)
        write_progress(status="error", message=message, last_event=detail)
        return runner._error_result(
            candidate.candidate_id,
            message,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
        )

    def cancelled_result(message: str, stdout_tail: str = "", stderr_tail: str = ""):
        detail = runner._tail(stderr_tail or stdout_tail or message, max_lines=8, max_chars=1200)
        runner._refresh_resume_pending_jobs(run_dir, current_run_id=search_run_id)
        write_progress(status="cancelled", stage="done", message=message, last_event=detail)
        return result_factory(
            success=False,
            exit_code=-2,
            message=message,
            stdout_tail=runner._tail(stdout_tail),
            stderr_tail=runner._tail(stderr_tail),
            run_dir=run_dir,
            cancelled=True,
        )

    write_progress(
        status="running",
        stage=progress_state["current_stage"],
        message="Preparing search runtime.",
        last_event="Initializing search workspace.",
    )
    if cancel_event is not None and cancel_event.is_set():
        return cancelled_result("Search cancelled before start.")

    semantic_profile = None
    candidate_search_signals = None
    candidate_runtime_context = None
    effective_max_companies = max(1, int(max_companies))
    model_overrides = runtime_config_builder.resolve_model_overrides(settings)
    model_override = model_overrides.fast_model

    try:
        base_config = runtime_config_builder.load_base_config()
        semantic_profile = load_candidate_semantic_profile_for_run(
            runner,
            candidate=candidate,
            settings=settings,
            api_base_url=api_base_url,
            run_dir=run_dir,
        )
        runner._store_semantic_profile_snapshot(
            candidate_id=candidate.candidate_id,
            semantic_profile=semantic_profile,
        )
        candidate_search_signals = candidate_search_signals_module.collect_candidate_search_signals(
            profiles=profiles,
            semantic_profile=semantic_profile,
        )
        candidate_runtime_context = runtime_config_builder.build_runtime_candidate_context(
            candidate=candidate,
            profiles=profiles,
            run_dir=run_dir,
            semantic_profile=semantic_profile,
            signals=candidate_search_signals,
        )
        if semantic_profile is not None and semantic_profile.is_usable():
            write_progress(
                status="running",
                stage=progress_state["current_stage"],
                message="Preparing search runtime with AI semantic profile.",
                last_event="AI-derived business profile loaded for company discovery and query planning.",
            )
        runtime_config = runtime_config_builder.build_runtime_config(
            getattr(runner, "runtime_mirror", None),
            base_config=base_config,
            candidate=candidate,
            profiles=profiles,
            run_dir=run_dir,
            query_rotation_seed=query_rotation_seed,
            semantic_profile=semantic_profile,
            model_override=model_override,
            quality_model_override=model_overrides.quality_model,
            pipeline_stage="main",
            signals=candidate_search_signals,
            candidate_context=candidate_runtime_context,
        )
        effective_max_companies = runtime_config_builder.resolve_effective_max_companies(
            requested_max_companies=effective_max_companies,
            runtime_config=runtime_config,
        )
        current_main_runtime_config = runtime_config
        adaptive_runtime_config = runtime_config_builder.ensure_dict(
            runtime_config,
            "adaptiveSearch",
        )

        resume_config = runtime_config_builder.build_runtime_config(
            getattr(runner, "runtime_mirror", None),
            base_config=base_config,
            candidate=candidate,
            profiles=profiles,
            run_dir=run_dir,
            query_rotation_seed=query_rotation_seed,
            semantic_profile=semantic_profile,
            model_override=model_override,
            quality_model_override=model_overrides.quality_model,
            pipeline_stage="resume_pending",
            signals=candidate_search_signals,
            candidate_context=candidate_runtime_context,
        )
        runner._sync_search_run_configs(
            search_run_id,
            runtime_config=runtime_config,
        )
    except Exception as exc:
        return error_result_for_run(f"Failed to generate runtime config: {exc}")

    env = runtime_config_builder.build_runtime_env(settings=settings, api_base_url=api_base_url)
    if not env.get("OPENAI_API_KEY", "").strip():
        return error_result_for_run(
            "OpenAI API key is missing. Set OPENAI_API_KEY or save it in AI settings.",
        )
    if cancel_event is not None and cancel_event.is_set():
        return cancelled_result("Search cancelled before execution.")

    session_outcome = search_session_orchestrator.run_search_session(
        search_session_orchestrator.SearchSessionRuntime(
            runner=runner,
            candidate_id=candidate.candidate_id,
            candidate=candidate,
            profiles=profiles,
            run_dir=run_dir,
            base_config=base_config,
            resume_config=resume_config,
            current_main_runtime_config=current_main_runtime_config,
            semantic_profile=semantic_profile,
            model_override=model_override,
            env=env,
            cancel_event=cancel_event,
            write_progress=write_progress,
            progress_state=progress_state,
            max_companies=max_companies,
            effective_max_companies=effective_max_companies,
            query_rotation_seed=query_rotation_seed,
            search_session_deadline=time_monotonic_fn() + max(1, int(timeout_seconds)),
            candidate_search_signals=candidate_search_signals,
            candidate_context=candidate_runtime_context,
            search_run_id=search_run_id,
        )
    )
    return result_factory(
        success=session_outcome.success,
        exit_code=session_outcome.exit_code,
        message=session_outcome.message,
        stdout_tail=session_outcome.stdout_tail,
        stderr_tail=session_outcome.stderr_tail,
        run_dir=run_dir,
        cancelled=session_outcome.cancelled,
        details=getattr(session_outcome, "details", None),
    )


__all__ = [
    "candidate_run_dir",
    "error_result",
    "load_candidate_semantic_profile_for_run",
    "load_search_progress_from_run_dir",
    "run_search",
    "tail",
    "write_search_progress",
]
