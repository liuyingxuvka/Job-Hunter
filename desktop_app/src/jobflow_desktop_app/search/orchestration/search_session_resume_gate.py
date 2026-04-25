from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .search_session_runtime import (
    SearchSessionOutcome,
    SearchSessionRuntime,
    _cancelled_outcome,
    _combined_tail,
    _refresh_resume_pending_jobs,
    _run_resume_stage,
)


@dataclass(frozen=True)
class ResumeGateResult:
    pending_after_round: int
    resume_phase_failed: bool
    early_outcome: SearchSessionOutcome | None = None


@dataclass(frozen=True)
class FinalizeGateResult:
    pending_after_round: int
    finalize_phase_failed: bool
    finalize_status: Literal["not_needed", "cleared", "incomplete"]
    cancelled_outcome: SearchSessionOutcome | None = None


def _run_resume_queue_gate(
    runtime: SearchSessionRuntime,
    *,
    pending_after_round: int,
    stage_name: Literal["resume", "finalize"],
    phase_message: str,
    start_event: str,
    cancel_message: str,
    failure_note: str,
    stalled_note: str,
    retry_note: str,
    stage_stdout: list[tuple[str, str]],
    stage_stderr: list[tuple[str, str]],
    stage_notes: list[str],
    block_on_failure: bool,
) -> tuple[int, bool, SearchSessionOutcome | None]:
    phase_failed = False
    if pending_after_round <= 0:
        return pending_after_round, phase_failed, None
    if runtime.cancel_event is not None and runtime.cancel_event.is_set():
        return (
            pending_after_round,
            phase_failed,
            _cancelled_outcome(
                runtime,
                cancel_message,
            ),
        )
    phase_result = _run_resume_stage(
        runtime,
        phase_message,
        start_event,
        stage_name=stage_name,
    )
    if phase_result is None:
        return pending_after_round, phase_failed, None
    if phase_result.stdout_tail:
        stage_stdout.append((stage_name, phase_result.stdout_tail))
    if phase_result.stderr_tail:
        stage_stderr.append((stage_name, phase_result.stderr_tail))
    if phase_result.cancelled:
        return (
            pending_after_round,
            phase_failed,
            _cancelled_outcome(
                runtime,
                cancel_message,
                stdout_tail=phase_result.stdout_tail,
                stderr_tail=phase_result.stderr_tail,
            ),
        )
    if not phase_result.success:
        stage_notes.append(
            f"{failure_note} (exit {phase_result.exit_code})."
        )
        phase_failed = bool(block_on_failure)
        try:
            pending_after_round = _refresh_resume_pending_jobs(runtime)
        except Exception:
            pass
        return pending_after_round, phase_failed, None
    try:
        next_pending = _refresh_resume_pending_jobs(runtime)
    except Exception as exc:
        stage_notes.append(f"Resume queue final check skipped: {exc}")
        return pending_after_round, phase_failed, None
    if next_pending >= pending_after_round and next_pending > 0:
        stage_notes.append(stalled_note)
    elif next_pending > 0:
        stage_notes.append(retry_note)
    pending_after_round = next_pending
    return pending_after_round, phase_failed, None


def run_initial_resume_gate(
    runtime: SearchSessionRuntime,
    stage_notes: list[str],
    stage_stdout: list[tuple[str, str]],
    stage_stderr: list[tuple[str, str]],
) -> ResumeGateResult:
    try:
        resume_pending_count = _refresh_resume_pending_jobs(runtime)
    except Exception as exc:
        message = (
            "Search stopped before discovery because the unfinished-job queue "
            f"could not be refreshed: {exc}"
        )
        stage_notes.append(message)
        runtime.write_progress(
            status="error",
            stage="done",
            message=message,
            last_event=message,
        )
        return ResumeGateResult(
            pending_after_round=0,
            resume_phase_failed=True,
            early_outcome=SearchSessionOutcome(
                success=False,
                exit_code=1,
                message=message,
                stdout_tail="",
                stderr_tail="",
            ),
        )
    if resume_pending_count > 0:
        stage_notes.append(
            f"Resume queue contains {resume_pending_count} unfinished job(s) before discovery."
        )

    pending_after_round = resume_pending_count
    resume_phase_failed = False
    if resume_pending_count <= 0:
        return ResumeGateResult(
            pending_after_round=pending_after_round,
            resume_phase_failed=resume_phase_failed,
        )
    pending_after_round, resume_phase_failed, cancelled_outcome = _run_resume_queue_gate(
        runtime,
        pending_after_round=pending_after_round,
        stage_name="resume",
        phase_message="Completing unfinished main-stage jobs before discovery.",
        start_event="Starting resume phase.",
        cancel_message="Search cancelled while resuming unfinished jobs.",
        failure_note="Resume phase failed; discovery did not start",
        stalled_note="Resume queue did not shrink on this pass; discovery will continue with queued jobs left.",
        retry_note="Resume queue shrank on this pass; discovery will continue with queued jobs left.",
        stage_stdout=stage_stdout,
        stage_stderr=stage_stderr,
        stage_notes=stage_notes,
        block_on_failure=False,
    )
    if cancelled_outcome is not None:
        return ResumeGateResult(
            pending_after_round=pending_after_round,
            resume_phase_failed=resume_phase_failed,
            early_outcome=cancelled_outcome,
        )

    if resume_phase_failed:
        message = "Search stopped before discovery because unfinished jobs remain queued."
        if stage_notes:
            message = f"{message} {' '.join(stage_notes)}"
        runtime.write_progress(
            status="error",
            stage="done",
            message=message,
            last_event=message,
        )
        return ResumeGateResult(
            pending_after_round=pending_after_round,
            resume_phase_failed=resume_phase_failed,
            early_outcome=SearchSessionOutcome(
                success=False,
                exit_code=1,
                message=message,
                stdout_tail=_combined_tail(runtime, stage_stdout),
                stderr_tail=_combined_tail(runtime, stage_stderr),
            ),
        )

    return ResumeGateResult(
        pending_after_round=pending_after_round,
        resume_phase_failed=resume_phase_failed,
    )


def run_finalize_resume_gate(
    runtime: SearchSessionRuntime,
    pending_after_round: int,
    stage_notes: list[str],
    stage_stdout: list[tuple[str, str]],
    stage_stderr: list[tuple[str, str]],
) -> FinalizeGateResult:
    if pending_after_round <= 0:
        return FinalizeGateResult(
            pending_after_round=pending_after_round,
            finalize_phase_failed=False,
            finalize_status="not_needed",
        )
    pending_after_round, finalize_phase_failed, cancelled_outcome = _run_resume_queue_gate(
        runtime,
        pending_after_round=pending_after_round,
        stage_name="finalize",
        phase_message="Finalizing unfinished jobs before the next discovery round.",
        start_event="Starting finalize phase.",
        cancel_message="Search cancelled while finalizing unfinished jobs.",
        failure_note="Finalize phase failed; remaining unfinished jobs stay queued",
        stalled_note="Finalize queue did not shrink on this pass; continuing only if the round made other progress.",
        retry_note="Finalize queue shrank on this pass; continuing while time remains.",
        stage_stdout=stage_stdout,
        stage_stderr=stage_stderr,
        stage_notes=stage_notes,
        block_on_failure=False,
    )
    if cancelled_outcome is not None:
        return FinalizeGateResult(
            pending_after_round=pending_after_round,
            finalize_phase_failed=finalize_phase_failed,
            finalize_status="incomplete",
            cancelled_outcome=cancelled_outcome,
        )
    finalize_status: Literal["not_needed", "cleared", "incomplete"] = (
        "cleared" if not finalize_phase_failed and pending_after_round <= 0 else "incomplete"
    )

    return FinalizeGateResult(
        pending_after_round=pending_after_round,
        finalize_phase_failed=finalize_phase_failed,
        finalize_status=finalize_status,
    )


__all__ = [
    "FinalizeGateResult",
    "ResumeGateResult",
    "run_finalize_resume_gate",
    "run_initial_resume_gate",
]
