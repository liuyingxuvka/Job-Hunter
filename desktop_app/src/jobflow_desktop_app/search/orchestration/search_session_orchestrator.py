from __future__ import annotations

from dataclasses import dataclass

from .. import runtime_strategy
from ..companies.selection import select_companies_for_run
from .runtime_config_builder import ensure_dict
from .search_session_resume_gate import (
    run_finalize_resume_gate,
    run_initial_resume_gate,
)
from .search_session_runtime import (
    SearchSessionOutcome,
    SearchSessionRuntime,
    _StageResult,
    _cancelled_outcome,
    _combined_tail,
    _refresh_python_recommended_outputs,
    _refresh_resume_pending_jobs,
    _remaining_search_session_seconds,
    _run_company_discovery_stage,
    _run_company_selection_stage,
    _run_company_sources_stage,
    _run_resume_stage,
    _set_stage,
    _write_main_runtime_config,
)


@dataclass(frozen=True)
class RoundProgress:
    company_pool_growth: int
    job_growth: int
    pending_drop: int
    recommended_job_growth: int

    @property
    def made_progress(self) -> bool:
        return any(
            value > 0
            for value in (
                self.company_pool_growth,
                self.job_growth,
                self.pending_drop,
                self.recommended_job_growth,
            )
        )


@dataclass(frozen=True)
class CompanyRoundOutcome:
    main_result: _StageResult
    cancelled_outcome: SearchSessionOutcome | None = None


@dataclass(frozen=True)
class DiscoveryRoundOutcome:
    main_result: _StageResult
    pending_after_round: int
    round_progress: RoundProgress
    attempted_query_discovery: bool = False
    finalize_phase_failed: bool = False
    finalize_status: str = "not_needed"
    cancelled_outcome: SearchSessionOutcome | None = None


def _refresh_round_outputs(
    runtime: SearchSessionRuntime,
    stage_notes: list[str],
    *,
    config_override: dict | None,
    failure_note: str,
) -> None:
    try:
        _refresh_python_recommended_outputs(
            runtime,
            config_override,
        )
    except Exception as exc:
        stage_notes.append(f"{failure_note}: {exc}")


def _round_stage_label(base_name: str, round_number: int) -> str:
    return base_name if round_number <= 1 else f"{base_name}_round_{round_number}"


def _record_stage_result_or_cancel(
    runtime: SearchSessionRuntime,
    stage_result: _StageResult | None,
    *,
    stage_label: str,
    cancel_message: str,
    stage_stdout: list[tuple[str, str]],
    stage_stderr: list[tuple[str, str]],
) -> SearchSessionOutcome | None:
    if stage_result is None:
        return None
    if stage_result.stdout_tail:
        stage_stdout.append((stage_label, stage_result.stdout_tail))
    if stage_result.stderr_tail:
        stage_stderr.append((stage_label, stage_result.stderr_tail))
    if not stage_result.cancelled:
        return None
    return _cancelled_outcome(
        runtime,
        cancel_message,
        stdout_tail=stage_result.stdout_tail,
        stderr_tail=stage_result.stderr_tail,
    )


def _run_round_stage(
    runtime: SearchSessionRuntime,
    stage_result: _StageResult | None,
    *,
    stage_label: str,
    cancel_message: str,
    stage_stdout: list[tuple[str, str]],
    stage_stderr: list[tuple[str, str]],
) -> SearchSessionOutcome | None:
    return _record_stage_result_or_cancel(
        runtime,
        stage_result,
        stage_label=stage_label,
        cancel_message=cancel_message,
        stage_stdout=stage_stdout,
        stage_stderr=stage_stderr,
    )


def _resolve_selected_companies_for_sources(
    runtime: SearchSessionRuntime,
    company_selection_result: _StageResult | None,
) -> list[dict[str, object]]:
    if (
        company_selection_result is not None
        and company_selection_result.success
        and isinstance(company_selection_result.payload, dict)
    ):
        return [
            dict(item)
            for item in company_selection_result.payload.get("selectedCompanies", [])
            if isinstance(item, dict)
        ]
    if runtime.runner.runtime_mirror is None:
        return []
    candidate_pool = runtime.runner.runtime_mirror.load_candidate_company_pool(
        candidate_id=runtime.candidate_id,
    )
    return select_companies_for_run(
        config=runtime.current_main_runtime_config,
        companies=candidate_pool,
        max_companies=max(0, int(runtime.effective_max_companies)),
    )


def _measure_round_progress(search_stats_before_round, search_stats_after_round) -> RoundProgress:
    return RoundProgress(
        company_pool_growth=max(
            0,
            search_stats_after_round.candidate_company_pool_count
            - search_stats_before_round.candidate_company_pool_count,
        ),
        job_growth=max(
            0,
            search_stats_after_round.main_discovered_job_count
            - search_stats_before_round.main_discovered_job_count,
        ),
        pending_drop=max(
            0,
            search_stats_before_round.main_pending_analysis_count
            - search_stats_after_round.main_pending_analysis_count,
        ),
        recommended_job_growth=max(
            0,
            search_stats_after_round.recommended_job_count
            - search_stats_before_round.recommended_job_count,
        ),
    )


def _next_empty_round_count(empty_rounds: int, progress: RoundProgress) -> int:
    return 0 if progress.made_progress else empty_rounds + 1


def _round_message(round_number: int, *, first: str, later: str) -> str:
    return first if round_number <= 1 else later.format(round_number=round_number)


def _round_company_budget(
    *,
    round_number: int,
    company_pool_before_round: int,
    discovery_breadth: int,
) -> tuple[int, int]:
    if company_pool_before_round <= 0 or round_number > 1:
        return discovery_breadth, discovery_breadth
    return 0, 0


def _main_result_from_remaining_selected_companies(
    *,
    round_number: int,
    remaining_selected_companies: list[dict[str, object]],
    stage_notes: list[str],
) -> _StageResult:
    if not remaining_selected_companies:
        return _StageResult(
            success=True,
            exit_code=0,
            message=(
                "Residual discovery skipped because Python company sourcing "
                "completed the current selected company batch."
            ),
            stdout_tail="",
            stderr_tail="",
        )
    stage_notes.append(
        "Python company sourcing left "
        f"{len(remaining_selected_companies)} company(s) in the current selected batch "
        f"after round {round_number}."
    )
    return _StageResult(
        success=True,
        exit_code=0,
        message="Residual company discovery deferred until the next company selection cycle.",
        stdout_tail="",
        stderr_tail="",
    )


def _run_company_round(
    runtime: SearchSessionRuntime,
    *,
    round_number: int,
    company_pool_before_round: int,
    discovery_breadth: int,
    stage_notes: list[str],
    stage_stdout: list[tuple[str, str]],
    stage_stderr: list[tuple[str, str]],
) -> CompanyRoundOutcome:
    company_query_budget, company_cap = _round_company_budget(
        round_number=round_number,
        company_pool_before_round=company_pool_before_round,
        discovery_breadth=discovery_breadth,
    )
    company_discovery_result = _run_company_discovery_stage(
        runtime,
        _round_message(
            round_number,
            first="Refreshing company pool before discovery round 1.",
            later="Refreshing company pool before discovery round {round_number}.",
        ),
        _round_message(
            round_number,
            first="Starting Python company discovery round 1.",
            later="Starting Python company discovery round {round_number}.",
        ),
        query_budget=company_query_budget,
        max_new_companies=company_cap,
    )
    cancelled_outcome = _run_round_stage(
        runtime,
        company_discovery_result,
        stage_label=_round_stage_label("company_discovery", round_number),
        cancel_message="Search cancelled while refreshing the company pool.",
        stage_stdout=stage_stdout,
        stage_stderr=stage_stderr,
    )
    if cancelled_outcome is not None:
        return CompanyRoundOutcome(
            main_result=_StageResult(
                success=False,
                exit_code=-2,
                message="Search cancelled while refreshing the company pool.",
                stdout_tail=company_discovery_result.stdout_tail if company_discovery_result is not None else "",
                stderr_tail=company_discovery_result.stderr_tail if company_discovery_result is not None else "",
                cancelled=True,
            ),
            cancelled_outcome=cancelled_outcome,
        )
    if company_discovery_result is not None and not company_discovery_result.success:
        if company_pool_before_round <= 0:
            return CompanyRoundOutcome(
                main_result=_StageResult(
                    success=False,
                    exit_code=company_discovery_result.exit_code,
                    message=company_discovery_result.message,
                    stdout_tail=company_discovery_result.stdout_tail,
                    stderr_tail=company_discovery_result.stderr_tail,
                )
            )
        stage_notes.append(
            "Python company discovery failed "
            f"(exit {company_discovery_result.exit_code}); continuing with the existing company pool."
        )

    company_selection_result = _run_company_selection_stage(
        runtime,
        _round_message(
            round_number,
            first="Selecting companies for discovery round 1.",
            later="Selecting companies for discovery round {round_number}.",
        ),
        _round_message(
            round_number,
            first="Starting Python company selection round 1.",
            later="Starting Python company selection round {round_number}.",
        ),
    )
    cancelled_outcome = _run_round_stage(
        runtime,
        company_selection_result,
        stage_label=_round_stage_label("company_selection", round_number),
        cancel_message="Search cancelled while selecting companies for discovery.",
        stage_stdout=stage_stdout,
        stage_stderr=stage_stderr,
    )
    if cancelled_outcome is not None:
        return CompanyRoundOutcome(
            main_result=_StageResult(
                success=False,
                exit_code=-2,
                message="Search cancelled while selecting companies for discovery.",
                stdout_tail=company_selection_result.stdout_tail if company_selection_result is not None else "",
                stderr_tail=company_selection_result.stderr_tail if company_selection_result is not None else "",
                cancelled=True,
            ),
            cancelled_outcome=cancelled_outcome,
        )
    if company_selection_result is not None and not company_selection_result.success:
        stage_notes.append(
            "Python company selection failed "
            f"(exit {company_selection_result.exit_code}); falling back to the current candidate company pool."
        )

    selected_companies_for_sources = _resolve_selected_companies_for_sources(
        runtime,
        company_selection_result,
    )
    company_sources_result = _run_company_sources_stage(
        runtime,
        _round_message(
            round_number,
            first="Fetching direct ATS jobs before discovery round 1.",
            later="Fetching direct ATS jobs before discovery round {round_number}.",
        ),
        _round_message(
            round_number,
            first="Starting Python company sources round 1.",
            later="Starting Python company sources round {round_number}.",
        ),
        selected_companies=selected_companies_for_sources,
    )
    cancelled_outcome = _run_round_stage(
        runtime,
        company_sources_result,
        stage_label=_round_stage_label("company_sources", round_number),
        cancel_message="Search cancelled while fetching direct ATS company jobs.",
        stage_stdout=stage_stdout,
        stage_stderr=stage_stderr,
    )
    if cancelled_outcome is not None:
        return CompanyRoundOutcome(
            main_result=_StageResult(
                success=False,
                exit_code=-2,
                message="Search cancelled while fetching direct ATS company jobs.",
                stdout_tail=company_sources_result.stdout_tail if company_sources_result is not None else "",
                stderr_tail=company_sources_result.stderr_tail if company_sources_result is not None else "",
                cancelled=True,
            ),
            cancelled_outcome=cancelled_outcome,
        )

    remaining_selected_companies: list[dict[str, object]] = []
    if company_sources_result is not None:
        if not company_sources_result.success:
            stage_notes.append(
                "Python company sources failed "
                f"(exit {company_sources_result.exit_code}); deferring residual company processing to the next selection cycle."
            )
        if isinstance(company_sources_result.payload, dict):
            remaining_selected_companies = [
                dict(item)
                for item in company_sources_result.payload.get(
                    "remainingSelectedCompanies",
                    [],
                )
                if isinstance(item, dict)
            ]
    return CompanyRoundOutcome(
        main_result=_main_result_from_remaining_selected_companies(
            round_number=round_number,
            remaining_selected_companies=remaining_selected_companies,
            stage_notes=stage_notes,
        )
    )


def _run_discovery_round(
    runtime: SearchSessionRuntime,
    *,
    round_number: int,
    search_stats_before_round,
    stage_notes: list[str],
    stage_stdout: list[tuple[str, str]],
    stage_stderr: list[tuple[str, str]],
) -> DiscoveryRoundOutcome:
    attempted_query_discovery = (
        search_stats_before_round.candidate_company_pool_count <= 0
        or round_number > 1
    )
    company_round_outcome = _run_company_round(
        runtime,
        round_number=round_number,
        company_pool_before_round=search_stats_before_round.candidate_company_pool_count,
        discovery_breadth=runtime_strategy.positive_int(
            ensure_dict(runtime.current_main_runtime_config, "adaptiveSearch").get("discoveryBreadth"),
            runtime_strategy.ADAPTIVE_SEARCH_HIGH_LEVEL_DEFAULTS["discoveryBreadth"],
        ),
        stage_notes=stage_notes,
        stage_stdout=stage_stdout,
        stage_stderr=stage_stderr,
    )
    main_result = company_round_outcome.main_result
    if company_round_outcome.cancelled_outcome is not None:
            return DiscoveryRoundOutcome(
                main_result=main_result,
                pending_after_round=search_stats_before_round.main_pending_analysis_count,
                round_progress=RoundProgress(0, 0, 0, 0),
                attempted_query_discovery=attempted_query_discovery,
                cancelled_outcome=company_round_outcome.cancelled_outcome,
            )
    stage_label = _round_stage_label("discover", round_number)
    if main_result.stdout_tail:
        stage_stdout.append((stage_label, main_result.stdout_tail))
    if main_result.stderr_tail:
        stage_stderr.append((stage_label, main_result.stderr_tail))
    if not main_result.success:
        return DiscoveryRoundOutcome(
            main_result=main_result,
            pending_after_round=search_stats_before_round.main_pending_analysis_count,
            round_progress=RoundProgress(0, 0, 0, 0),
            attempted_query_discovery=attempted_query_discovery,
        )

    try:
        pending_after_round = _refresh_resume_pending_jobs(runtime)
    except Exception as exc:
        stage_notes.append(f"Resume queue finalize skipped: {exc}")
        return DiscoveryRoundOutcome(
            main_result=main_result,
            pending_after_round=search_stats_before_round.main_pending_analysis_count,
            round_progress=RoundProgress(0, 0, 0, 0),
            attempted_query_discovery=attempted_query_discovery,
            finalize_phase_failed=True,
        )

    finalize_phase_failed = False
    finalize_status = "not_needed"
    if pending_after_round > 0:
        finalize_gate_result = run_finalize_resume_gate(
            runtime,
            pending_after_round,
            stage_notes,
            stage_stdout,
            stage_stderr,
        )
        pending_after_round = finalize_gate_result.pending_after_round
        finalize_phase_failed = finalize_gate_result.finalize_phase_failed
        finalize_status = finalize_gate_result.finalize_status
        if finalize_gate_result.cancelled_outcome is not None:
            return DiscoveryRoundOutcome(
                main_result=main_result,
                pending_after_round=pending_after_round,
                round_progress=RoundProgress(0, 0, 0, 0),
                attempted_query_discovery=attempted_query_discovery,
                finalize_phase_failed=finalize_phase_failed,
                finalize_status=finalize_status,
                cancelled_outcome=finalize_gate_result.cancelled_outcome,
            )
        if finalize_phase_failed or pending_after_round > 0:
            _refresh_round_outputs(
                runtime,
                stage_notes,
                config_override=runtime.current_main_runtime_config,
                failure_note="Python recommended output refresh skipped after finalize round",
            )
            return DiscoveryRoundOutcome(
                main_result=main_result,
                pending_after_round=pending_after_round,
                round_progress=RoundProgress(0, 0, 0, 0),
                attempted_query_discovery=attempted_query_discovery,
                finalize_phase_failed=finalize_phase_failed,
                finalize_status=finalize_status,
            )

    _refresh_round_outputs(
        runtime,
        stage_notes,
        config_override=runtime.current_main_runtime_config,
        failure_note="Python recommended output refresh skipped after discovery round",
    )
    search_stats_after_round = runtime.runner.load_search_stats(runtime.candidate_id)
    return DiscoveryRoundOutcome(
        main_result=main_result,
        pending_after_round=pending_after_round,
        round_progress=_measure_round_progress(
            search_stats_before_round,
            search_stats_after_round,
        ),
        attempted_query_discovery=attempted_query_discovery,
        finalize_phase_failed=finalize_phase_failed,
        finalize_status=finalize_status,
    )


def run_search_session(runtime: SearchSessionRuntime) -> SearchSessionOutcome:
    stage_notes: list[str] = []
    stage_stdout: list[tuple[str, str]] = []
    stage_stderr: list[tuple[str, str]] = []
    main_result: _StageResult | None = None

    resume_gate_result = run_initial_resume_gate(
        runtime,
        stage_notes,
        stage_stdout,
        stage_stderr,
    )
    pending_after_round = resume_gate_result.pending_after_round
    resume_phase_failed = resume_gate_result.resume_phase_failed
    if resume_gate_result.early_outcome is not None:
        _refresh_round_outputs(
            runtime,
            stage_notes,
            config_override=runtime.resume_config,
            failure_note="Python recommended output refresh skipped before early stop",
        )
        return resume_gate_result.early_outcome

    empty_rounds = 0
    attempted_query_discovery = False
    completed_pending_finalize_count = 0
    incomplete_pending_finalize_count = 0
    current_round_number = 0
    finalize_phase_failed = False
    while True:
        if runtime.cancel_event is not None and runtime.cancel_event.is_set():
            return _cancelled_outcome(
                runtime,
                "Search cancelled before starting the next discovery round.",
            )
        remaining_budget_seconds = _remaining_search_session_seconds(runtime)
        if remaining_budget_seconds <= 0:
            if current_round_number <= 0 and pending_after_round <= 0:
                main_result = _StageResult(
                    success=False,
                    exit_code=1,
                    message="Timed search session reached its configured duration before discovery could start.",
                    stdout_tail="",
                    stderr_tail="",
                )
            else:
                stage_notes.append("Timed search session reached its configured duration.")
            break

        pass_query_rotation_seed = runtime.query_rotation_seed + (
            current_round_number * 104729
        )
        if current_round_number > 0:
            try:
                _write_main_runtime_config(runtime, pass_query_rotation_seed)
            except Exception as exc:
                main_result = _StageResult(
                    success=False,
                    exit_code=1,
                    message=(
                        "Timed search session stopped because refreshed company discovery queries "
                        f"could not be prepared: {exc}"
                    ),
                    stdout_tail="",
                    stderr_tail="",
                )
                break

        search_stats_before_round = runtime.runner.load_search_stats(runtime.candidate_id)
        current_round_number += 1
        round_outcome = _run_discovery_round(
            runtime,
            round_number=current_round_number,
            search_stats_before_round=search_stats_before_round,
            stage_notes=stage_notes,
            stage_stdout=stage_stdout,
            stage_stderr=stage_stderr,
        )
        if round_outcome.cancelled_outcome is not None:
            return round_outcome.cancelled_outcome
        main_result = round_outcome.main_result
        if not main_result.success:
            if main_result.cancelled:
                return _cancelled_outcome(
                    runtime,
                    "Search cancelled while running discovery.",
                    stdout_tail=main_result.stdout_tail,
                    stderr_tail=main_result.stderr_tail,
                )
            break
        pending_after_round = round_outcome.pending_after_round
        attempted_query_discovery = (
            attempted_query_discovery or round_outcome.attempted_query_discovery
        )
        finalize_phase_failed = round_outcome.finalize_phase_failed
        if round_outcome.finalize_status == "cleared":
            completed_pending_finalize_count += 1
        elif round_outcome.finalize_status == "incomplete":
            incomplete_pending_finalize_count += 1
        if finalize_phase_failed or pending_after_round > 0:
            break

        round_progress = round_outcome.round_progress
        empty_rounds = _next_empty_round_count(empty_rounds, round_progress)
        if attempted_query_discovery and empty_rounds >= runtime.empty_rounds_before_end:
            stage_notes.append(
                f"Timed search session ended after {runtime.empty_rounds_before_end} consecutive rounds without progress."
            )
            break

    success = (
        pending_after_round <= 0
        and not resume_phase_failed
        and not finalize_phase_failed
        and (main_result is None or main_result.success)
    )
    if success:
        if main_result is None:
            message = "Timed search session completed after clearing pending jobs."
        else:
            message = "Timed search session completed."
        runtime.runner._clear_resume_pending_jobs(runtime.run_dir)
    elif main_result is not None and main_result.success and pending_after_round > 0:
        message = "Timed search session stopped before finishing all discovered jobs."
    else:
        message = (main_result.message if main_result is not None else "") or (
            "Timed search session failed."
        )
    if completed_pending_finalize_count > 0:
        stage_notes.append(
            f"Pending jobs were finalized successfully in {completed_pending_finalize_count} round(s)."
        )
    if incomplete_pending_finalize_count > 0:
        stage_notes.append(
            f"Pending jobs still remained after finalization in {incomplete_pending_finalize_count} round(s)."
        )
    if stage_notes:
        message = f"{message} {' '.join(stage_notes)}"

    try:
        _refresh_python_recommended_outputs(
            runtime,
            runtime.current_main_runtime_config,
        )
    except Exception as exc:
        stage_notes.append(f"Final Python recommended output refresh skipped: {exc}")
        message = f"{message} Final Python recommended output refresh skipped: {exc}"

    runtime.write_progress(
        status="success" if success else "error",
        stage="done",
        message=message,
        last_event=(
            (main_result.stderr_tail if main_result is not None else "")
            or (main_result.stdout_tail if main_result is not None else "")
            or message
        ),
    )
    return SearchSessionOutcome(
        success=success,
        exit_code=main_result.exit_code if main_result is not None else 0,
        message=message,
        stdout_tail=_combined_tail(runtime, stage_stdout),
        stderr_tail=_combined_tail(runtime, stage_stderr),
    )


__all__ = [
    "CompanyRoundOutcome",
    "DiscoveryRoundOutcome",
    "RoundProgress",
    "SearchSessionOutcome",
    "SearchSessionRuntime",
    "_measure_round_progress",
    "_next_empty_round_count",
    "_record_stage_result_or_cancel",
    "_run_round_stage",
    "_round_stage_label",
    "run_search_session",
]
