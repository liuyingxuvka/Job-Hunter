from __future__ import annotations

from dataclasses import dataclass

from ..companies.selection import (
    select_companies_for_run,
    unresolved_company_ranking_count,
)
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
    _mark_stage_log_status,
    _refresh_python_recommended_outputs,
    _refresh_resume_pending_jobs,
    _remaining_search_session_seconds,
    _run_company_discovery_stage,
    _run_company_selection_stage,
    _run_company_sources_stage,
    _run_direct_job_discovery_stage,
    _run_resume_stage,
    _set_stage,
    _write_main_runtime_config,
)

@dataclass(frozen=True)
class RoundProgress:
    company_pool_growth: int
    company_ranking_drop: int
    job_growth: int
    pending_drop: int
    recommended_job_growth: int

    @property
    def made_progress(self) -> bool:
        return any(
            value > 0
            for value in (
                self.company_pool_growth,
                self.company_ranking_drop,
                self.job_growth,
                self.pending_drop,
                self.recommended_job_growth,
            )
        )


@dataclass(frozen=True)
class CompanyRoundOutcome:
    main_result: _StageResult
    discovery_stage_attempted: bool = False
    session_details: dict[str, object] | None = None
    cancelled_outcome: SearchSessionOutcome | None = None


@dataclass(frozen=True)
class DiscoveryRoundOutcome:
    main_result: _StageResult
    pending_after_round: int
    round_progress: RoundProgress
    attempted_query_discovery: bool = False
    finalize_phase_failed: bool = False
    finalize_status: str = "not_needed"
    session_details: dict[str, object] | None = None
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
    if not stage_result.cancelled and not (
        runtime.cancel_event is not None and runtime.cancel_event.is_set()
    ):
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
        selected = [
            dict(item)
            for item in company_selection_result.payload.get("selectedCompanies", [])
            if isinstance(item, dict)
        ]
        return selected
    if runtime.runner.runtime_mirror is None:
        return []
    candidate_pool = runtime.runner.runtime_mirror.load_candidate_company_pool(
        candidate_id=runtime.candidate_id,
    )
    selected = select_companies_for_run(
        companies=candidate_pool,
        max_companies=max(0, int(runtime.effective_max_companies)),
        current_run_id=runtime.search_run_id,
    )
    return selected


def _skip_company_sources_result(message: str) -> _StageResult:
    return _StageResult(
        success=True,
        exit_code=0,
        message=message,
        stdout_tail="",
        stderr_tail="",
    )


def _has_ready_companies_for_sources(runtime: SearchSessionRuntime) -> bool:
    if runtime.runner.runtime_mirror is None:
        return False
    candidate_pool = runtime.runner.runtime_mirror.load_candidate_company_pool(
        candidate_id=runtime.candidate_id,
    )
    selected = select_companies_for_run(
        companies=candidate_pool,
        max_companies=max(0, int(runtime.effective_max_companies)),
        current_run_id=runtime.search_run_id,
    )
    return bool(selected)


def _round_message(round_number: int, *, first: str, later: str) -> str:
    return first if round_number <= 1 else later.format(round_number=round_number)


def _measure_round_progress(
    runtime: SearchSessionRuntime,
    *,
    search_stats_before_round,
    unresolved_company_rankings_before_round: int,
) -> RoundProgress:
    search_stats_after_round = runtime.runner.load_search_stats(runtime.candidate_id)
    unresolved_company_rankings_after_round = 0
    if runtime.runner.runtime_mirror is not None:
        companies_after_round = runtime.runner.runtime_mirror.load_candidate_company_pool(
            candidate_id=runtime.candidate_id,
        )
        unresolved_company_rankings_after_round = unresolved_company_ranking_count(
            companies_after_round,
            current_run_id=runtime.search_run_id,
        )
    return RoundProgress(
        company_pool_growth=max(
            0,
            search_stats_after_round.candidate_company_pool_count
            - search_stats_before_round.candidate_company_pool_count,
        ),
        company_ranking_drop=max(
            0,
            unresolved_company_rankings_before_round
            - unresolved_company_rankings_after_round,
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
    unresolved_company_rankings_before_round: int,
    ready_companies_for_sources_before_round: bool,
    stage_notes: list[str],
    stage_stdout: list[tuple[str, str]],
    stage_stderr: list[tuple[str, str]],
) -> CompanyRoundOutcome:
    discovery_stage_attempted = False
    company_discovery_result: _StageResult | None = None
    if company_pool_before_round > 0 and unresolved_company_rankings_before_round > 0:
        stage_notes.append(
            "Python company discovery deferred until company fit ranking finishes for the current pool."
        )
    elif company_pool_before_round > 0 and ready_companies_for_sources_before_round:
        stage_notes.append(
            "Python company discovery deferred because scored companies are already ready for the sources stage."
        )
    else:
        discovery_stage_attempted = True
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
            round_number=round_number,
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
                discovery_stage_attempted=discovery_stage_attempted,
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
            _mark_stage_log_status(
                runtime,
                company_discovery_result,
                status="soft_failed",
                message=stage_notes[-1],
                error_summary=company_discovery_result.stderr_tail,
            )
        if (
            company_discovery_result is not None
            and company_discovery_result.success
            and isinstance(company_discovery_result.payload, dict)
            and bool(company_discovery_result.payload.get("noQualifiedNewCompanies"))
        ):
            stage_notes.append(
                "Company discovery did not produce qualified new companies in this round; continuing with existing company-pool work."
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
        round_number=round_number,
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
        _mark_stage_log_status(
            runtime,
            company_selection_result,
            status="soft_failed",
            message=stage_notes[-1],
            error_summary=company_selection_result.stderr_tail,
        )

    selected_companies_for_sources = _resolve_selected_companies_for_sources(
        runtime,
        company_selection_result,
    )
    if not selected_companies_for_sources:
        waiting_on_company_ranking = False
        if runtime.runner.runtime_mirror is not None:
            candidate_pool = runtime.runner.runtime_mirror.load_candidate_company_pool(
                candidate_id=runtime.candidate_id,
            )
            waiting_on_company_ranking = (
                unresolved_company_ranking_count(
                    candidate_pool,
                    current_run_id=runtime.search_run_id,
                ) > 0
            )
        if waiting_on_company_ranking:
            stage_notes.append(
                "Python company sources deferred until company fit ranking finishes for the current pool."
            )
            return CompanyRoundOutcome(
                main_result=_skip_company_sources_result(
                    "Company fit ranking still pending; sources stage deferred."
                ),
                discovery_stage_attempted=discovery_stage_attempted,
            )
        return CompanyRoundOutcome(
            main_result=_skip_company_sources_result(
                "No companies selected for company sources this round."
            ),
            discovery_stage_attempted=discovery_stage_attempted,
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
        round_number=round_number,
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
            _mark_stage_log_status(
                runtime,
                company_sources_result,
                status="soft_failed",
                message=stage_notes[-1],
                error_summary=company_sources_result.stderr_tail,
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
        ),
        discovery_stage_attempted=discovery_stage_attempted,
    )


def _run_discovery_round(
    runtime: SearchSessionRuntime,
    *,
    round_number: int,
    search_stats_before_round,
    unresolved_company_rankings_before_round: int,
    stage_notes: list[str],
    stage_stdout: list[tuple[str, str]],
    stage_stderr: list[tuple[str, str]],
) -> DiscoveryRoundOutcome:
    direct_job_result = _run_direct_job_discovery_stage(
        runtime,
        _round_message(
            round_number,
            first="Searching direct job opportunities before company round 1.",
            later="Searching direct job opportunities before company round {round_number}.",
        ),
        _round_message(
            round_number,
            first="Starting Python direct job discovery round 1.",
            later="Starting Python direct job discovery round {round_number}.",
        ),
        round_number=round_number,
    )
    direct_cancelled_outcome = _run_round_stage(
        runtime,
        direct_job_result,
        stage_label=_round_stage_label("direct_job_discovery", round_number),
        cancel_message="Search cancelled while running direct job discovery.",
        stage_stdout=stage_stdout,
        stage_stderr=stage_stderr,
    )
    if direct_cancelled_outcome is not None:
        return DiscoveryRoundOutcome(
            main_result=_StageResult(
                success=False,
                exit_code=-2,
                message="Search cancelled while running direct job discovery.",
                stdout_tail=direct_job_result.stdout_tail if direct_job_result is not None else "",
                stderr_tail=direct_job_result.stderr_tail if direct_job_result is not None else "",
                cancelled=True,
            ),
            pending_after_round=search_stats_before_round.main_pending_analysis_count,
            round_progress=RoundProgress(0, 0, 0, 0, 0),
            attempted_query_discovery=False,
            cancelled_outcome=direct_cancelled_outcome,
        )
    if direct_job_result is not None and not direct_job_result.success:
        stage_notes.append(
            "Python direct job discovery failed "
            f"(exit {direct_job_result.exit_code}); continuing with the existing company-pool flow."
        )
        _mark_stage_log_status(
            runtime,
            direct_job_result,
            status="soft_failed",
            message=stage_notes[-1],
            error_summary=direct_job_result.stderr_tail,
        )

    search_stats_before_company_round = search_stats_before_round
    unresolved_company_rankings_for_company_round = unresolved_company_rankings_before_round
    if direct_job_result is not None:
        search_stats_before_company_round = runtime.runner.load_search_stats(runtime.candidate_id)
    if direct_job_result is not None and runtime.runner.runtime_mirror is not None:
        companies_before_company_round = runtime.runner.runtime_mirror.load_candidate_company_pool(
            candidate_id=runtime.candidate_id,
        )
        unresolved_company_rankings_for_company_round = unresolved_company_ranking_count(
            companies_before_company_round,
            current_run_id=runtime.search_run_id,
        )
    ready_companies_for_sources_before_round = (
        unresolved_company_rankings_for_company_round <= 0
        and _has_ready_companies_for_sources(runtime)
    )
    company_round_outcome = _run_company_round(
        runtime,
        round_number=round_number,
        company_pool_before_round=search_stats_before_company_round.candidate_company_pool_count,
        unresolved_company_rankings_before_round=unresolved_company_rankings_for_company_round,
        ready_companies_for_sources_before_round=ready_companies_for_sources_before_round,
        stage_notes=stage_notes,
        stage_stdout=stage_stdout,
        stage_stderr=stage_stderr,
    )
    attempted_query_discovery = company_round_outcome.discovery_stage_attempted
    main_result = company_round_outcome.main_result
    if company_round_outcome.cancelled_outcome is not None:
        return DiscoveryRoundOutcome(
            main_result=main_result,
            pending_after_round=search_stats_before_round.main_pending_analysis_count,
            round_progress=RoundProgress(0, 0, 0, 0, 0),
            attempted_query_discovery=attempted_query_discovery,
            session_details=company_round_outcome.session_details,
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
            round_progress=RoundProgress(0, 0, 0, 0, 0),
            attempted_query_discovery=attempted_query_discovery,
            session_details=company_round_outcome.session_details,
        )
    if company_round_outcome.session_details is not None:
        return DiscoveryRoundOutcome(
            main_result=main_result,
            pending_after_round=search_stats_before_round.main_pending_analysis_count,
            round_progress=RoundProgress(0, 0, 0, 0, 0),
            attempted_query_discovery=attempted_query_discovery,
            session_details=company_round_outcome.session_details,
        )

    try:
        pending_after_round = _refresh_resume_pending_jobs(runtime)
    except Exception as exc:
        stage_notes.append(f"Resume queue finalize skipped: {exc}")
        return DiscoveryRoundOutcome(
            main_result=main_result,
            pending_after_round=search_stats_before_round.main_pending_analysis_count,
            round_progress=RoundProgress(0, 0, 0, 0, 0),
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
                round_progress=RoundProgress(0, 0, 0, 0, 0),
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
                round_progress=_measure_round_progress(
                    runtime,
                    search_stats_before_round=search_stats_before_round,
                    unresolved_company_rankings_before_round=unresolved_company_rankings_before_round,
                ),
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
    return DiscoveryRoundOutcome(
        main_result=main_result,
        pending_after_round=pending_after_round,
        round_progress=_measure_round_progress(
            runtime,
            search_stats_before_round=search_stats_before_round,
            unresolved_company_rankings_before_round=unresolved_company_rankings_before_round,
        ),
        attempted_query_discovery=attempted_query_discovery,
        finalize_phase_failed=finalize_phase_failed,
        finalize_status=finalize_status,
        session_details=company_round_outcome.session_details,
    )


def run_search_session(runtime: SearchSessionRuntime) -> SearchSessionOutcome:
    stage_notes: list[str] = []
    stage_stdout: list[tuple[str, str]] = []
    stage_stderr: list[tuple[str, str]] = []
    main_result: _StageResult | None = None
    session_details: dict[str, object] | None = None

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
        unresolved_company_rankings_before_round = 0
        if runtime.runner.runtime_mirror is not None:
            companies_before_round = runtime.runner.runtime_mirror.load_candidate_company_pool(
                candidate_id=runtime.candidate_id,
            )
            unresolved_company_rankings_before_round = unresolved_company_ranking_count(
                companies_before_round,
                current_run_id=runtime.search_run_id,
            )
        current_round_number += 1
        round_outcome = _run_discovery_round(
            runtime,
            round_number=current_round_number,
            search_stats_before_round=search_stats_before_round,
            unresolved_company_rankings_before_round=unresolved_company_rankings_before_round,
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
        if round_outcome.session_details is not None:
            session_details = round_outcome.session_details
            if (
                str(round_outcome.session_details.get("stopReason") or "").strip()
                == "no_qualified_new_companies"
            ):
                stage_notes.append(
                    "Company discovery did not produce qualified new companies in this session. Try again later."
                )
            break
        finalize_phase_failed = round_outcome.finalize_phase_failed
        if round_outcome.finalize_status == "cleared":
            completed_pending_finalize_count += 1
        elif round_outcome.finalize_status == "incomplete":
            incomplete_pending_finalize_count += 1
        if finalize_phase_failed:
            break
        if pending_after_round > 0:
            if round_outcome.round_progress.made_progress:
                stage_notes.append(
                    "Pending jobs remain after this round, but the session made progress; continuing while time remains."
                )
                continue
            break

        if (
            not round_outcome.round_progress.made_progress
            and not finalize_phase_failed
            and pending_after_round <= 0
            and runtime.runner.runtime_mirror is not None
        ):
            current_pool = runtime.runner.runtime_mirror.load_candidate_company_pool(
                candidate_id=runtime.candidate_id,
            )
            unresolved_rankings = unresolved_company_ranking_count(
                current_pool,
                current_run_id=runtime.search_run_id,
            )
            ready_companies = _has_ready_companies_for_sources(runtime)
            if unresolved_rankings <= 0 and not ready_companies:
                stage_notes.append(
                    "Timed search session ended because no actionable work units remained for this session."
                )
                break

    success = (
        not resume_phase_failed
        and not finalize_phase_failed
        and (main_result is None or main_result.success)
    )
    if success:
        if main_result is None:
            message = "Timed search session completed after clearing pending jobs."
        else:
            message = "Timed search session completed."
        if pending_after_round <= 0:
            runtime.runner._clear_resume_pending_jobs(
                runtime.run_dir,
                current_run_id=runtime.search_run_id,
            )
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
    if success and pending_after_round > 0:
        stage_notes.append(
            f"Pending jobs remain queued for a later manual session ({pending_after_round} job(s))."
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
        details=session_details,
    )


__all__ = [
    "CompanyRoundOutcome",
    "DiscoveryRoundOutcome",
    "RoundProgress",
    "SearchSessionOutcome",
    "SearchSessionRuntime",
    "_measure_round_progress",
    "_record_stage_result_or_cancel",
    "_run_round_stage",
    "_round_stage_label",
    "run_search_session",
]
