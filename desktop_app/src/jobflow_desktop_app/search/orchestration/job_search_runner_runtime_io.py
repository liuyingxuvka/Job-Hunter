from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

from ...ai.role_recommendations import CandidateSemanticProfile
from ..output.final_output import materialize_output_eligibility, rebuild_recommended_output_payload
from ..output.manual_tracking_store import overlay_manual_fields_onto_jobs
from ..output.tracker_xlsx import write_tracker_xlsx
from ..state.runtime_run_locator import (
    candidate_id_from_run_dir,
    job_review_state_repository_for_run_dir,
)
from ..state.search_progress_state import SearchProgress, SearchStats
from . import job_search_runner_records
from . import job_search_runner_session
from . import job_result_i18n


def _latest_runtime_config(runner, candidate_id: int) -> dict:
    if runner.runtime_mirror is None:
        return {}
    try:
        payload = runner.runtime_mirror.load_run_config(
            candidate_id=int(candidate_id),
        )
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_recommended_jobs(runner, candidate_id: int, *, job_result_factory) -> list:
    if runner.runtime_mirror is None:
        return []
    config = _latest_runtime_config(runner, candidate_id)
    jobs = runner.runtime_mirror.load_candidate_bucket_jobs_merged(
        candidate_id=int(candidate_id),
        job_bucket="recommended",
    )
    jobs = [
        materialize_output_eligibility(item, config)
        for item in jobs
        if bool((item.get("analysis") or {}).get("recommend"))
    ]
    jobs = job_search_runner_records.filter_displayable_recommended_jobs(
        jobs,
        config=config,
    )
    if not jobs:
        all_jobs = runner.runtime_mirror.load_candidate_bucket_jobs_merged(
            candidate_id=int(candidate_id),
            job_bucket="all",
        )
        recommended_in_all = [
            materialize_output_eligibility(item, config)
            for item in all_jobs
            if bool((item.get("analysis") or {}).get("recommend"))
        ]
        jobs = job_search_runner_records.filter_displayable_recommended_jobs(
            recommended_in_all,
            config=config,
        )
    if not jobs:
        return []
    jobs = job_result_i18n.enrich_job_display_i18n(runner, candidate_id, jobs)
    return job_search_runner_records.build_job_records(
        jobs,
        job_result_factory=job_result_factory,
    )


def load_live_jobs(runner, candidate_id: int, *, job_result_factory) -> list:
    if runner.runtime_mirror is None:
        return []
    merged_jobs: dict[str, dict] = {}
    for bucket in ("found", "all", "recommended"):
        bucket_jobs = runner.runtime_mirror.load_candidate_bucket_jobs_merged(
            candidate_id=int(candidate_id),
            job_bucket=bucket,
        )
        for item in bucket_jobs:
            if not isinstance(item, dict):
                continue
            key = runner._job_item_key(item)
            if not key:
                continue
            existing = merged_jobs.get(key)
            merged_jobs[key] = (
                runner._merge_job_item(existing, item) if existing is not None else dict(item)
            )
    if not merged_jobs:
        return []
    merged_job_list = job_result_i18n.enrich_job_display_i18n(
        runner,
        candidate_id,
        list(merged_jobs.values()),
    )
    return job_search_runner_records.build_job_records(
        job_search_runner_records.filter_live_review_jobs(
            merged_job_list
        ),
        job_result_factory=job_result_factory,
    )


def load_search_stats(runner, candidate_id: int) -> SearchStats:
    if runner.runtime_mirror is None:
        return SearchStats()
    config = _latest_runtime_config(runner, candidate_id)
    found_jobs = runner.runtime_mirror.load_latest_bucket_jobs(
        candidate_id=int(candidate_id),
        job_bucket="found",
    )
    all_jobs = runner.runtime_mirror.load_latest_bucket_jobs(
        candidate_id=int(candidate_id),
        job_bucket="all",
    )
    recommended_jobs = runner.runtime_mirror.load_latest_bucket_jobs(
        candidate_id=int(candidate_id),
        job_bucket="recommended",
    )
    pending_jobs = runner.runtime_mirror.load_latest_bucket_jobs(
        candidate_id=int(candidate_id),
        job_bucket="resume_pending",
    )
    merged_jobs: dict[str, dict] = {}
    for item in [*found_jobs, *all_jobs, *recommended_jobs]:
        if not isinstance(item, dict):
            continue
        key = runner._job_item_key(item)
        if not key:
            continue
        existing = merged_jobs.get(key)
        merged_jobs[key] = (
            runner._merge_job_item(existing, item) if existing is not None else dict(item)
        )
    main_jobs = list(merged_jobs.values())
    candidate_company_pool_count = runner.runtime_mirror.count_candidate_company_pool(
        int(candidate_id)
    )
    discovered_companies = {
        str(item.get("company") or "").strip().casefold()
        for item in main_jobs
        if str(item.get("company") or "").strip()
    }
    scored_jobs = job_search_runner_records.filter_live_review_jobs(main_jobs)
    materialized_recommended_jobs = [
        item
        for item in (
            materialize_output_eligibility(item, config)
            for item in recommended_jobs
            if bool((item.get("analysis") or {}).get("recommend"))
        )
    ]
    displayable_result_count = len(
        job_search_runner_records.filter_displayable_recommended_jobs(
            materialized_recommended_jobs,
            config=config,
        )
    )
    return SearchStats(
        discovered_job_count=len(main_jobs),
        discovered_company_count=len(discovered_companies),
        scored_job_count=len(scored_jobs),
        recommended_job_count=displayable_result_count,
        pending_resume_count=len(pending_jobs),
        candidate_company_pool_count=candidate_company_pool_count,
        signal_hit_job_count=0,
        main_discovered_job_count=len(main_jobs),
        main_scored_job_count=len(scored_jobs),
        displayable_result_count=displayable_result_count,
        main_pending_analysis_count=len(pending_jobs),
    )


def load_search_progress(runner, candidate_id: int) -> SearchProgress:
    if runner.runtime_mirror is None:
        return SearchProgress()
    payload = runner.runtime_mirror.load_latest_progress_payload(int(candidate_id))
    if not isinstance(payload, dict):
        return SearchProgress()
    started_at = str(payload.get("startedAt") or "").strip()
    updated_at = str(payload.get("updatedAt") or "").strip()
    elapsed_seconds = 0
    if started_at:
        try:
            started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            elapsed_seconds = max(
                0,
                int((datetime.now(timezone.utc) - started_dt).total_seconds()),
            )
        except Exception:
            elapsed_seconds = 0
    return SearchProgress(
        status=str(payload.get("status") or "idle").strip() or "idle",
        stage=str(payload.get("stage") or "idle").strip() or "idle",
        message=str(payload.get("message") or "").strip(),
        last_event=str(payload.get("lastEvent") or "").strip(),
        started_at=started_at,
        updated_at=updated_at,
        elapsed_seconds=elapsed_seconds,
    )


def refresh_python_recommended_output_json(
    runner,
    run_dir: Path,
    config: dict | None,
) -> int:
    candidate_id = candidate_id_from_run_dir(run_dir)
    runtime_mirror = getattr(runner, "runtime_mirror", None)
    search_run_id = None
    if runtime_mirror is not None and candidate_id is not None:
        latest_run = runtime_mirror.latest_run(candidate_id)
        if latest_run is not None:
            search_run_id = latest_run.search_run_id
        else:
            try:
                search_run_id = runtime_mirror.create_run(
                    candidate_id=candidate_id,
                    run_dir=run_dir,
                    status="success",
                    current_stage="done",
                    started_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                )
            except Exception:
                search_run_id = None
    if runtime_mirror is None or search_run_id is None or candidate_id is None:
        return 0
    all_jobs = (
        runtime_mirror.load_run_bucket_jobs(
            search_run_id=search_run_id,
            job_bucket="all",
        )
        if search_run_id is not None
        else []
    )
    existing_recommended_jobs = (
        runtime_mirror.load_run_bucket_jobs(
            search_run_id=search_run_id,
            job_bucket="recommended",
        )
        if search_run_id is not None
        else []
    )
    if not all_jobs and not existing_recommended_jobs:
        runtime_mirror.replace_bucket_jobs(
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            job_bucket="recommended",
            jobs=[],
        )
        write_tracker_xlsx(
            xlsx_path=run_dir / "jobs_recommended.xlsx",
            jobs=[],
            manual_by_url={},
            config=config or {},
        )
        return 0
    tracker_xlsx_path = run_dir / "jobs_recommended.xlsx"
    review_states = job_review_state_repo_for_run_dir(run_dir)
    manual_by_alias: dict[str, dict[str, str]] = {}
    if review_states is not None and candidate_id is not None:
        manual_by_alias = review_states.load_candidate_manual_alias_map(candidate_id)
    all_jobs = overlay_manual_fields_onto_jobs(all_jobs, manual_by_alias)
    existing_recommended_jobs = overlay_manual_fields_onto_jobs(
        existing_recommended_jobs,
        manual_by_alias,
    )
    result = rebuild_recommended_output_payload(
        all_jobs=all_jobs,
        existing_recommended_jobs=existing_recommended_jobs,
        config=config or {},
    )
    payload_jobs = result.payload.get("jobs", [])
    if isinstance(payload_jobs, list):
        payload_jobs = overlay_manual_fields_onto_jobs(payload_jobs, manual_by_alias)
        result.payload["jobs"] = payload_jobs
        if review_states is not None and candidate_id is not None:
            review_states.merge_manual_fields_from_jobs(
                candidate_id=candidate_id,
                jobs=payload_jobs,
            )
            manual_by_alias = review_states.load_candidate_manual_alias_map(candidate_id)
    runtime_mirror.replace_bucket_jobs(
        search_run_id=search_run_id,
        candidate_id=candidate_id,
        job_bucket="recommended",
        jobs=payload_jobs if isinstance(payload_jobs, list) else [],
    )
    if isinstance(payload_jobs, list):
        write_tracker_xlsx(
            xlsx_path=tracker_xlsx_path,
            jobs=payload_jobs,
            manual_by_url=manual_by_alias,
            config=config or {},
        )
    return len(payload_jobs) if isinstance(payload_jobs, list) else 0
job_review_state_repo_for_run_dir = job_review_state_repository_for_run_dir


def create_search_run(
    runner,
    *,
    candidate_id: int,
    run_dir: Path,
    status: str,
    current_stage: str,
    started_at: str,
) -> int | None:
    if runner.runtime_mirror is None:
        return None
    try:
        return runner.runtime_mirror.create_run(
            candidate_id=candidate_id,
            run_dir=run_dir,
            status=status,
            current_stage=current_stage,
            started_at=started_at,
        )
    except Exception:
        return None


def write_search_progress(
    runner,
    run_dir: Path,
    *,
    status: str,
    stage: str,
    message: str = "",
    last_event: str = "",
    started_at: str = "",
    search_run_id: int | None = None,
) -> None:
    if runner.runtime_mirror is None:
        return
    try:
        runner.runtime_mirror.update_progress(
            search_run_id,
            status=status,
            stage=stage,
            message=message,
            last_event=last_event,
            started_at=started_at,
        )
    except Exception:
        return


def sync_search_run_configs(
    runner,
    search_run_id: int | None,
    *,
    runtime_config: dict | None = None,
) -> None:
    if runner.runtime_mirror is None:
        return
    try:
        runner.runtime_mirror.update_configs(
            search_run_id,
            runtime_config=runtime_config,
        )
    except Exception:
        return


def store_semantic_profile_snapshot(
    runner,
    *,
    candidate_id: int,
    semantic_profile: CandidateSemanticProfile | None,
) -> None:
    if runner.runtime_mirror is None or semantic_profile is None:
        return
    if not semantic_profile.is_usable():
        return
    try:
        runner.runtime_mirror.store_semantic_profile(
            candidate_id=candidate_id,
            profile_payload=semantic_profile.to_payload(),
        )
    except Exception:
        return


def error_result(
    runner,
    candidate_id: int,
    message: str,
    *,
    stdout_tail: str = "",
    stderr_tail: str = "",
    result_factory,
):
    return job_search_runner_session.error_result(
        runner,
        candidate_id,
        message,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        result_factory=result_factory,
    )


def tail(text: str, max_lines: int = 40, max_chars: int = 4000) -> str:
    return job_search_runner_session.tail(text, max_lines=max_lines, max_chars=max_chars)


__all__ = [
    "candidate_id_from_run_dir",
    "create_search_run",
    "error_result",
    "job_review_state_repo_for_run_dir",
    "load_live_jobs",
    "load_recommended_jobs",
    "load_search_progress",
    "load_search_stats",
    "refresh_python_recommended_output_json",
    "store_semantic_profile_snapshot",
    "sync_search_run_configs",
    "tail",
    "write_search_progress",
]
