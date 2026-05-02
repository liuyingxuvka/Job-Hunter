from __future__ import annotations
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from ...ai.role_recommendations import CandidateSemanticProfile
from ..companies.company_sources_enrichment import fetch_job_details
from ..output.final_output import (
    MATERIALIZED_OUTPUT_SOURCE,
    build_final_output_dedupe_key,
    choose_output_job_url,
    decide_source_aware_final_recommendation_visibility,
    enrich_recommended_job,
    has_unavailable_url_signal,
    is_unavailable_job,
    materialize_output_eligibility,
    rebuild_recommended_output_payload,
)
from ..output.manual_tracking_store import overlay_manual_fields_onto_jobs
from ..output.tracker_xlsx import write_tracker_xlsx
from ..run_state import job_identity_key, job_item_key
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


def _merge_runtime_config(base: dict | None, override: dict | None) -> dict:
    merged = dict(base) if isinstance(base, dict) else {}
    if not isinstance(override, dict):
        return merged
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_runtime_config(merged.get(key), value)
        else:
            merged[key] = value
    return merged


def _merge_jobs_by_runtime_key(runner, jobs: list[dict]) -> list[dict]:
    merged_jobs: dict[str, dict] = {}
    for item in jobs:
        if not isinstance(item, dict):
            continue
        key = runner._job_item_key(item)
        if not key:
            continue
        existing = merged_jobs.get(key)
        merged_jobs[key] = (
            runner._merge_job_item(existing, item) if existing is not None else dict(item)
        )
    return list(merged_jobs.values())


def _final_output_link_recheck_timeout_seconds(config: dict | None) -> int:
    raw_fetch_config = config.get("fetch") if isinstance(config, Mapping) else {}
    fetch_config = raw_fetch_config if isinstance(raw_fetch_config, Mapping) else {}
    try:
        timeout_ms = int(fetch_config.get("timeoutMs") or 12000)
    except (TypeError, ValueError):
        timeout_ms = 12000
    return max(3, min(15, int(timeout_ms / 1000) or 12))


def _merge_output_link_details(job: dict, details: Mapping[str, object], output_url: str) -> dict:
    merged = dict(job)
    existing_jd = merged.get("jd")
    jd = dict(existing_jd) if isinstance(existing_jd, Mapping) else {}
    jd.update(
        {
            "fetchedAt": str(details.get("fetchedAt") or datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
            "ok": bool(details.get("ok")),
            "status": int(details.get("status") or 0),
            "finalUrl": str(details.get("finalUrl") or output_url).strip(),
            "redirected": bool(details.get("redirected")),
            "rawText": str(details.get("rawText") or jd.get("rawText") or ""),
            "applyUrl": str(details.get("applyUrl") or jd.get("applyUrl") or ""),
            "outputLinkRechecked": True,
        }
    )
    merged["jd"] = jd
    merged["outputUrl"] = output_url
    return merged


def _hard_invalid_output_link(details: Mapping[str, object], output_url: str) -> bool:
    try:
        status = int(details.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    final_url = str(details.get("finalUrl") or output_url or "").strip()
    if status in {404, 410, 451}:
        return True
    return has_unavailable_url_signal(output_url) or has_unavailable_url_signal(final_url)


def _job_runtime_key(item: Mapping[str, object]) -> str:
    payload = dict(item)
    return job_item_key(payload) or job_identity_key(payload)


def _recheck_recommended_output_links(
    jobs: list[dict],
    config: dict | None,
) -> tuple[list[dict], dict[str, str]]:
    checked_jobs: list[dict] = []
    drop_reasons: dict[str, str] = {}
    for item in jobs:
        if not isinstance(item, dict):
            continue
        runtime_key = _job_runtime_key(item)
        output_url = str(item.get("outputUrl") or choose_output_job_url(item, config) or "").strip()
        if not output_url:
            if runtime_key:
                drop_reasons[runtime_key] = "missing_output_url"
            continue
        details = fetch_job_details(
            output_url,
            config=config,
            timeout_seconds=_final_output_link_recheck_timeout_seconds(config),
        )
        if _hard_invalid_output_link(details, output_url):
            if runtime_key:
                drop_reasons[runtime_key] = "link_recheck_failed"
            continue
        rechecked = _merge_output_link_details(item, details, output_url)
        if is_unavailable_job(rechecked, config):
            if runtime_key:
                drop_reasons[runtime_key] = "unavailable_after_link_recheck"
            continue
        materialized = materialize_output_eligibility(rechecked, config)
        decision = decide_source_aware_final_recommendation_visibility(
            materialized,
            config,
            source=MATERIALIZED_OUTPUT_SOURCE,
        )
        if decision.visible:
            checked_jobs.append(materialized)
        elif runtime_key:
            drop_reasons[runtime_key] = decision.reason or "output_recheck_failed"
    return checked_jobs, drop_reasons


def _build_recommended_output_drop_reasons(
    *,
    all_jobs: list[Mapping[str, object]],
    selected_jobs: list[Mapping[str, object]],
    config: dict | None,
) -> dict[str, str]:
    selected_runtime_keys = {
        _job_runtime_key(item)
        for item in selected_jobs
        if isinstance(item, Mapping) and _job_runtime_key(item)
    }
    selected_final_keys = {
        build_final_output_dedupe_key(item, config)
        for item in selected_jobs
        if isinstance(item, Mapping) and build_final_output_dedupe_key(item, config)
    }
    reasons: dict[str, str] = {}
    for item in all_jobs:
        if not isinstance(item, Mapping):
            continue
        runtime_key = _job_runtime_key(item)
        if not runtime_key or runtime_key in selected_runtime_keys:
            continue
        analysis = item.get("analysis")
        if not isinstance(analysis, Mapping) or analysis.get("recommend") is not True:
            continue
        stamped = materialize_output_eligibility(enrich_recommended_job(item, config), config)
        decision = decide_source_aware_final_recommendation_visibility(
            stamped,
            config,
            source=MATERIALIZED_OUTPUT_SOURCE,
        )
        if decision.visible:
            final_key = build_final_output_dedupe_key(stamped, config)
            reasons[runtime_key] = "duplicate_merged" if final_key in selected_final_keys else "not_in_final_output_set"
        else:
            reasons[runtime_key] = decision.reason or "not_output_eligible"
    return reasons


def _load_cumulative_bucket_jobs(
    runner,
    candidate_id: int,
    *,
    buckets: tuple[str, ...],
) -> list[dict]:
    if runner.runtime_mirror is None:
        return []
    jobs: list[dict] = []
    for bucket in buckets:
        jobs.extend(
            runner.runtime_mirror.load_candidate_bucket_jobs_merged(
                candidate_id=int(candidate_id),
                job_bucket=bucket,
            )
        )
    return _merge_jobs_by_runtime_key(runner, jobs)


def _load_cumulative_main_jobs(runner, candidate_id: int) -> list[dict]:
    pool_loader = getattr(runner.runtime_mirror, "load_candidate_job_pool_payloads", None)
    if callable(pool_loader):
        jobs = pool_loader(candidate_id=int(candidate_id))
        if jobs:
            return jobs
    return _load_cumulative_bucket_jobs(
        runner,
        candidate_id,
        buckets=("found", "all", "recommended"),
    )


def _displayable_recommended_jobs(jobs: list[dict], *, config: dict) -> list[dict]:
    return job_search_runner_records.filter_displayable_recommended_jobs(
        [
            item
            for item in jobs
            if isinstance(item, dict) and bool((item.get("analysis") or {}).get("recommend"))
        ],
        config=config,
    )


def load_recommended_jobs(runner, candidate_id: int, *, job_result_factory) -> list:
    if runner.runtime_mirror is None:
        return []
    config = _latest_runtime_config(runner, candidate_id)
    pool_loader = getattr(runner.runtime_mirror, "load_candidate_recommended_job_pool_payloads", None)
    if callable(pool_loader):
        jobs = _displayable_recommended_jobs(
            pool_loader(candidate_id=int(candidate_id)),
            config=config,
        )
    else:
        jobs = []
    if not jobs:
        jobs = _displayable_recommended_jobs(
            _load_cumulative_bucket_jobs(
                runner,
                candidate_id,
                buckets=("all", "recommended"),
            ),
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
    config = _latest_runtime_config(runner, candidate_id)
    jobs = _load_cumulative_main_jobs(runner, candidate_id)
    if not jobs:
        return []
    merged_job_list = job_result_i18n.enrich_job_display_i18n(
        runner,
        candidate_id,
        jobs,
    )
    return job_search_runner_records.build_job_records(
        job_search_runner_records.filter_live_review_jobs(
            merged_job_list,
            config=config,
        ),
        job_result_factory=job_result_factory,
    )


def load_search_stats(runner, candidate_id: int) -> SearchStats:
    if runner.runtime_mirror is None:
        return SearchStats()
    config = _latest_runtime_config(runner, candidate_id)
    pool_summary_loader = getattr(runner.runtime_mirror, "summarize_candidate_job_pool", None)
    pool_summary = None
    if callable(pool_summary_loader):
        pool_summary = pool_summary_loader(candidate_id=int(candidate_id))
    if pool_summary is not None and getattr(pool_summary, "total_jobs", 0):
        candidate_company_pool_count = runner.runtime_mirror.count_candidate_company_pool(
            int(candidate_id)
        )
        pool_jobs_loader = getattr(runner.runtime_mirror, "load_candidate_job_pool_payloads", None)
        pool_jobs = pool_jobs_loader(candidate_id=int(candidate_id)) if callable(pool_jobs_loader) else []
        discovered_companies = {
            str(item.get("company") or "").strip().casefold()
            for item in pool_jobs
            if isinstance(item, dict) and str(item.get("company") or "").strip()
        }
        return SearchStats(
            discovered_job_count=int(getattr(pool_summary, "total_jobs", 0) or 0),
            discovered_company_count=len(discovered_companies),
            scored_job_count=int(getattr(pool_summary, "scored_jobs", 0) or 0),
            recommended_job_count=int(getattr(pool_summary, "recommended_jobs", 0) or 0),
            pending_resume_count=int(getattr(pool_summary, "pending_jobs", 0) or 0),
            candidate_company_pool_count=candidate_company_pool_count,
            signal_hit_job_count=0,
            main_discovered_job_count=int(getattr(pool_summary, "total_jobs", 0) or 0),
            main_scored_job_count=int(getattr(pool_summary, "scored_jobs", 0) or 0),
            displayable_result_count=int(getattr(pool_summary, "recommended_jobs", 0) or 0),
            main_pending_analysis_count=int(getattr(pool_summary, "pending_jobs", 0) or 0),
        )
    main_jobs = _load_cumulative_main_jobs(runner, candidate_id)
    pending_jobs = runner.runtime_mirror.load_latest_bucket_jobs(
        candidate_id=int(candidate_id),
        job_bucket="resume_pending",
    )
    candidate_company_pool_count = runner.runtime_mirror.count_candidate_company_pool(
        int(candidate_id)
    )
    discovered_companies = {
        str(item.get("company") or "").strip().casefold()
        for item in main_jobs
        if str(item.get("company") or "").strip()
    }
    scored_jobs = job_search_runner_records.filter_live_review_jobs(main_jobs)
    displayable_result_count = len(_displayable_recommended_jobs(main_jobs, config=config))
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
    *,
    search_run_id: int | None = None,
) -> int:
    candidate_id = candidate_id_from_run_dir(run_dir)
    runtime_mirror = getattr(runner, "runtime_mirror", None)
    if search_run_id is None and runtime_mirror is not None and candidate_id is not None:
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
    effective_config = _merge_runtime_config(
        _latest_runtime_config(runner, int(candidate_id)),
        config or {},
    )
    pool_loader = getattr(runtime_mirror, "load_candidate_job_pool_payloads", None)
    recommended_pool_loader = getattr(
        runtime_mirror,
        "load_candidate_recommended_job_pool_payloads",
        None,
    )
    all_jobs = pool_loader(candidate_id=int(candidate_id)) if callable(pool_loader) else []
    existing_recommended_jobs = (
        recommended_pool_loader(candidate_id=int(candidate_id))
        if callable(recommended_pool_loader)
        else []
    )
    if not all_jobs:
        all_jobs = (
            runtime_mirror.load_run_bucket_jobs(
                search_run_id=search_run_id,
                job_bucket="all",
            )
            if search_run_id is not None
            else []
        )
    if not existing_recommended_jobs:
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
        marker = getattr(runtime_mirror, "mark_recommended_output_set", None)
        if callable(marker):
            marker(candidate_id=candidate_id, job_keys=set())
        write_tracker_xlsx(
            xlsx_path=run_dir / "jobs_recommended.xlsx",
            jobs=[],
            manual_by_url={},
            config=effective_config,
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
        config=effective_config,
    )
    payload_jobs = result.payload.get("jobs", [])
    output_drop_reasons: dict[str, str] = {}
    if isinstance(payload_jobs, list):
        output_drop_reasons = _build_recommended_output_drop_reasons(
            all_jobs=[item for item in all_jobs if isinstance(item, Mapping)],
            selected_jobs=[item for item in payload_jobs if isinstance(item, Mapping)],
            config=effective_config,
        )
        payload_jobs = overlay_manual_fields_onto_jobs(payload_jobs, manual_by_alias)
        payload_jobs, recheck_drop_reasons = _recheck_recommended_output_links(payload_jobs, effective_config)
        output_drop_reasons.update(recheck_drop_reasons)
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
    marker = getattr(runtime_mirror, "mark_recommended_output_set", None)
    if callable(marker):
        marker(
            candidate_id=candidate_id,
            job_keys={
                job_item_key(item) or job_identity_key(item)
                for item in payload_jobs
                if isinstance(item, dict)
            }
            if isinstance(payload_jobs, list)
            else set(),
            output_drop_reasons=output_drop_reasons,
        )
    if isinstance(payload_jobs, list):
        write_tracker_xlsx(
            xlsx_path=tracker_xlsx_path,
            jobs=payload_jobs,
            manual_by_url=manual_by_alias,
            config=effective_config,
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
