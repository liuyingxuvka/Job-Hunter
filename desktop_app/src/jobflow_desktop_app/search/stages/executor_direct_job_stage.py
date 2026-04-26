from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from ...ai.client import build_json_schema_request, build_text_input_messages, parse_response_json
from ...prompt_assets import load_prompt_asset
from ..analysis.service import JobAnalysisService, ResponseRequestClient
from ..companies.company_sources_enrichment import (
    build_found_job_records,
    merge_company_source_jobs,
)
from ..companies.discovery import build_company_identity_keys, merge_unique_strings, normalize_url
from ..companies.pool_store import merge_companies_into_master
from ..companies.sources_helpers import dedupe_jobs_by_normalized_url, has_job_signal
from ..output.final_output import (
    canonical_job_url,
    infer_region_tag,
    infer_source_quality,
    is_specific_job_detail_url,
    normalize_job_url,
)
from ..run_state import (
    analysis_completed,
    collect_resume_pending_jobs_from_job_lists,
    job_identity_key,
    job_item_key,
    merge_job_item,
)
from ..state.work_unit_state import clear_work_unit_state, record_technical_failure
from .executor import PythonStageRunResult
from .executor_common import _config_mapping, _now_iso, _relay_progress, _tail_lines
from .resume_pending_support import _build_data_availability_note, _load_candidate_profile_payload

DIRECT_JOB_DISCOVERY_PROMPT = load_prompt_asset("search_ranking", "direct_job_discovery_prompt.txt")
DIRECT_JOB_VERIFICATION_PROMPT = load_prompt_asset("search_ranking", "direct_job_verification_prompt.txt")

DIRECT_JOB_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "location": {"type": "string"},
                    "url": {"type": "string"},
                    "datePosted": {"type": "string"},
                    "summary": {"type": "string"},
                    "sourceHint": {"type": "string"},
                },
                "required": [
                    "title",
                    "company",
                    "location",
                    "url",
                    "datePosted",
                    "summary",
                    "sourceHint",
                ],
            },
        }
    },
    "required": ["jobs"],
}

DIRECT_JOB_VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "url": {"type": "string"},
                    "finalUrl": {"type": "string"},
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "location": {"type": "string"},
                    "datePosted": {"type": "string"},
                    "summary": {"type": "string"},
                    "isLiveJobPage": {"type": "boolean"},
                    "hasApplyEntry": {"type": "boolean"},
                    "applyUrl": {"type": "string"},
                    "fastFitScore": {"type": "integer", "minimum": 0, "maximum": 100},
                    "reason": {"type": "string"},
                },
                "required": [
                    "url",
                    "finalUrl",
                    "title",
                    "company",
                    "location",
                    "datePosted",
                    "summary",
                    "isLiveJobPage",
                    "hasApplyEntry",
                    "applyUrl",
                    "fastFitScore",
                    "reason",
                ],
            },
        }
    },
    "required": ["jobs"],
}


@dataclass(frozen=True)
class _DirectStageJobState:
    existing_all_jobs: list[dict[str, Any]]
    existing_found_jobs: list[dict[str, Any]]
    existing_recommended_jobs: list[dict[str, Any]]
    seen_keys: set[str]


def run_direct_job_discovery_stage_db(
    *,
    runtime_mirror,
    search_run_id: int,
    candidate_id: int,
    run_dir,
    config: dict,
    client_instance: ResponseRequestClient,
    cancel_event: Any | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> PythonStageRunResult:
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    direct_config = _config_mapping(config, "directJobDiscovery")
    max_jobs = max(1, int(_to_number(direct_config.get("maxJobsPerRound"), 10)))
    company_upsert_min_score = max(0, int(_to_number(direct_config.get("companyUpsertMinScore"), 60)))

    try:
        job_state = _load_direct_stage_job_state(
            runtime_mirror,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
        )
        discovered_jobs = discover_direct_jobs_for_candidate(
            client_instance,
            config=config,
            max_jobs=max_jobs,
        )
        if _cancel_requested(cancel_event):
            return _direct_cancelled_result(
                message="Python direct job discovery stage cancelled after discovery.",
                stdout_lines=stdout_lines,
                stderr_lines=stderr_lines,
            )
        _relay_progress(
            f"Python direct job discovery found {len(discovered_jobs)} candidate job(s).",
            stdout_lines,
            progress_callback,
        )

        new_candidates, skipped_existing = _filter_new_jobs(
            discovered_jobs,
            job_state.seen_keys,
            limit=max_jobs,
        )
        if not new_candidates:
            message = (
                "Python direct job discovery completed. "
                f"Found {len(discovered_jobs)} candidate job(s); skipped {skipped_existing} already-seen job(s); queued 0."
            )
            stdout_lines.append(message)
            return _direct_stage_result(
                message=message,
                stdout_lines=stdout_lines,
                stderr_lines=stderr_lines,
                raw_jobs=len(discovered_jobs),
                skipped_existing=skipped_existing,
            )

        live_jobs = _verify_new_direct_jobs(
            client_instance,
            config=config,
            jobs=new_candidates,
            seen_keys=job_state.seen_keys,
            limit=max_jobs,
        )
        if _cancel_requested(cancel_event):
            return _direct_cancelled_result(
                message="Python direct job discovery stage cancelled after verification.",
                stdout_lines=stdout_lines,
                stderr_lines=stderr_lines,
                raw_jobs=len(discovered_jobs),
                skipped_existing=skipped_existing,
                verified_jobs=len(live_jobs),
            )
        _relay_progress(
            f"Python direct job verification kept {len(live_jobs)} live new job(s).",
            stdout_lines,
            progress_callback,
        )

        scored_jobs, scoring_failures = _score_live_direct_jobs(
            client_instance,
            config=config,
            run_dir=run_dir,
            search_run_id=search_run_id,
            jobs=live_jobs,
            cancel_event=cancel_event,
        )
        if _cancel_requested(cancel_event):
            return _direct_cancelled_result(
                message="Python direct job discovery stage cancelled before saving scored jobs.",
                stdout_lines=stdout_lines,
                stderr_lines=stderr_lines,
                raw_jobs=len(discovered_jobs),
                skipped_existing=skipped_existing,
                verified_jobs=len(live_jobs),
                scored_jobs=len(scored_jobs),
            )
        _write_direct_job_buckets(
            runtime_mirror,
            job_state=job_state,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            config=config,
            scored_jobs=scored_jobs,
        )
        upserted_companies = _learn_companies_from_scored_jobs(
            runtime_mirror,
            candidate_id=candidate_id,
            scored_jobs=scored_jobs,
            min_score=company_upsert_min_score,
        )
        if hasattr(runtime_mirror, "refresh_counts"):
            runtime_mirror.refresh_counts(search_run_id=search_run_id)
    except Exception as exc:
        message = f"Python direct job discovery stage skipped after error: {exc}"
        stderr_lines.append(message)
        return _direct_stage_result(
            success=False,
            exit_code=1,
            message=message,
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines,
            error=str(exc),
        )

    recommended_count = sum(1 for job in scored_jobs if _job_is_recommended(job))
    message = (
        "Python direct job discovery completed. "
        f"Found {len(discovered_jobs)} candidate job(s); skipped {skipped_existing} already-seen job(s); "
        f"verified {len(live_jobs)} live job(s); scored {len(scored_jobs)}; "
        f"recommended {recommended_count}; upserted {upserted_companies} company(s)."
    )
    if scoring_failures > 0:
        message = f"{message} Suspended {scoring_failures} job(s) after scoring failures."
    stdout_lines.append(message)
    return _direct_stage_result(
        message=message,
        stdout_lines=stdout_lines,
        stderr_lines=stderr_lines,
        raw_jobs=len(discovered_jobs),
        skipped_existing=skipped_existing,
        verified_jobs=len(live_jobs),
        scored_jobs=len(scored_jobs),
        recommended_jobs=recommended_count,
        upserted_companies=upserted_companies,
    )


def _direct_stage_result(
    *,
    success: bool = True,
    exit_code: int = 0,
    message: str,
    stdout_lines: list[str],
    stderr_lines: list[str],
    raw_jobs: int = 0,
    skipped_existing: int = 0,
    verified_jobs: int = 0,
    scored_jobs: int = 0,
    recommended_jobs: int = 0,
    upserted_companies: int = 0,
    error: str = "",
) -> PythonStageRunResult:
    payload: dict[str, Any] = {
        "rawJobs": max(0, int(raw_jobs)),
        "skippedExisting": max(0, int(skipped_existing)),
        "verifiedJobs": max(0, int(verified_jobs)),
        "scoredJobs": max(0, int(scored_jobs)),
        "recommendedJobs": max(0, int(recommended_jobs)),
        "upsertedCompanies": max(0, int(upserted_companies)),
    }
    if error:
        payload["error"] = str(error)
    return PythonStageRunResult(
        success=success,
        exit_code=exit_code,
        message=message,
        stdout_tail=_tail_lines(stdout_lines),
        stderr_tail=_tail_lines(stderr_lines),
        payload=payload,
    )


def _direct_cancelled_result(
    *,
    message: str,
    stdout_lines: list[str],
    stderr_lines: list[str],
    raw_jobs: int = 0,
    skipped_existing: int = 0,
    verified_jobs: int = 0,
    scored_jobs: int = 0,
) -> PythonStageRunResult:
    stdout_lines.append(message)
    result = _direct_stage_result(
        success=False,
        exit_code=-2,
        message=message,
        stdout_lines=stdout_lines,
        stderr_lines=stderr_lines,
        raw_jobs=raw_jobs,
        skipped_existing=skipped_existing,
        verified_jobs=verified_jobs,
        scored_jobs=scored_jobs,
    )
    return PythonStageRunResult(
        success=result.success,
        exit_code=result.exit_code,
        message=result.message,
        stdout_tail=result.stdout_tail,
        stderr_tail=result.stderr_tail,
        cancelled=True,
        payload=result.payload,
    )


def _cancel_requested(cancel_event: Any | None) -> bool:
    return bool(cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())


def _load_direct_stage_job_state(
    runtime_mirror,
    *,
    search_run_id: int,
    candidate_id: int,
) -> _DirectStageJobState:
    existing_all_jobs = runtime_mirror.load_run_bucket_jobs(
        search_run_id=search_run_id,
        job_bucket="all",
    )
    existing_found_jobs = runtime_mirror.load_run_bucket_jobs(
        search_run_id=search_run_id,
        job_bucket="found",
    )
    existing_recommended_jobs = runtime_mirror.load_run_bucket_jobs(
        search_run_id=search_run_id,
        job_bucket="recommended",
    )
    historical_jobs = _load_historical_jobs(runtime_mirror, candidate_id)
    return _DirectStageJobState(
        existing_all_jobs=[dict(item) for item in existing_all_jobs if isinstance(item, Mapping)],
        existing_found_jobs=[dict(item) for item in existing_found_jobs if isinstance(item, Mapping)],
        existing_recommended_jobs=[dict(item) for item in existing_recommended_jobs if isinstance(item, Mapping)],
        seen_keys=_build_seen_job_keys(
            [*historical_jobs, *existing_all_jobs, *existing_found_jobs, *existing_recommended_jobs]
        ),
    )


def _verify_new_direct_jobs(
    client: ResponseRequestClient,
    *,
    config: Mapping[str, Any],
    jobs: list[Mapping[str, Any]],
    seen_keys: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    verified_jobs = verify_and_prerank_direct_jobs(
        client,
        config=config,
        jobs=jobs,
    )
    return [
        job
        for job in verified_jobs
        if _direct_job_is_live(job) and not _job_seen(job, seen_keys)
    ][: max(1, int(limit))]


def _score_live_direct_jobs(
    client: ResponseRequestClient,
    *,
    config: Mapping[str, Any],
    run_dir,
    search_run_id: int,
    jobs: list[Mapping[str, Any]],
    cancel_event: Any | None = None,
) -> tuple[list[dict[str, Any]], int]:
    candidate_profile = _load_candidate_profile_payload(config, run_dir)
    data_availability_note = _build_data_availability_note(candidate_profile)
    scored_jobs: list[dict[str, Any]] = []
    scoring_failures = 0
    for job in jobs:
        if _cancel_requested(cancel_event):
            break
        scored_job = _score_direct_job(
            client,
            config=config,
            candidate_profile=candidate_profile,
            data_availability_note=data_availability_note,
            search_run_id=search_run_id,
            job=job,
        )
        if not _analysis_completed(scored_job.get("analysis")):
            scoring_failures += 1
        scored_jobs.append(scored_job)
    return scored_jobs, scoring_failures


def _write_direct_job_buckets(
    runtime_mirror,
    *,
    job_state: _DirectStageJobState,
    search_run_id: int,
    candidate_id: int,
    config: Mapping[str, Any],
    scored_jobs: list[dict[str, Any]],
) -> None:
    all_jobs = merge_company_source_jobs(job_state.existing_all_jobs, scored_jobs)
    found_jobs = merge_company_source_jobs(
        job_state.existing_found_jobs,
        build_found_job_records(scored_jobs, existing_jobs=all_jobs, config=config),
    )
    recommended_jobs = merge_company_source_jobs(
        job_state.existing_recommended_jobs,
        [job for job in scored_jobs if _job_is_recommended(job)],
    )
    runtime_mirror.replace_bucket_jobs(
        search_run_id=search_run_id,
        candidate_id=candidate_id,
        job_bucket="all",
        jobs=all_jobs,
    )
    runtime_mirror.replace_bucket_jobs(
        search_run_id=search_run_id,
        candidate_id=candidate_id,
        job_bucket="found",
        jobs=found_jobs,
    )
    runtime_mirror.replace_bucket_jobs(
        search_run_id=search_run_id,
        candidate_id=candidate_id,
        job_bucket="recommended",
        jobs=recommended_jobs,
    )
    runtime_mirror.replace_bucket_jobs(
        search_run_id=search_run_id,
        candidate_id=candidate_id,
        job_bucket="resume_pending",
        jobs=collect_resume_pending_jobs_from_job_lists(
            all_jobs,
            recommended_jobs,
            current_run_id=search_run_id,
        ),
    )


def _learn_companies_from_scored_jobs(
    runtime_mirror,
    *,
    candidate_id: int,
    scored_jobs: list[Mapping[str, Any]],
    min_score: int,
) -> int:
    good_jobs = [
        job
        for job in scored_jobs
        if _job_quality_score(job) >= max(0, int(min_score)) and _analysis_completed(job.get("analysis"))
    ]
    return _upsert_direct_job_companies(
        runtime_mirror,
        candidate_id=candidate_id,
        jobs=good_jobs,
        discovered_at=_now_iso(),
    )


def discover_direct_jobs_for_candidate(
    client: ResponseRequestClient,
    *,
    config: Mapping[str, Any],
    max_jobs: int,
) -> list[dict[str, Any]]:
    search_config = config.get("search") if isinstance(config.get("search"), Mapping) else {}
    direct_config = config.get("directJobDiscovery") if isinstance(config.get("directJobDiscovery"), Mapping) else {}
    model = str(direct_config.get("model") or search_config.get("model") or "").strip()
    if not model:
        return []
    request = build_json_schema_request(
        model=model,
        input_payload=build_text_input_messages(
            "You find concrete current job detail pages and return only structured JSON.",
            DIRECT_JOB_DISCOVERY_PROMPT.format(
                candidate_context_json=json.dumps(_candidate_context_payload(config), ensure_ascii=False, indent=2),
                max_jobs=max(1, int(max_jobs)),
            ),
        ),
        schema_name="direct_job_discovery_results",
        schema=DIRECT_JOB_DISCOVERY_SCHEMA,
        use_web_search=True,
    )
    response = client.create(request)
    payload = parse_response_json(response, "Direct job discovery")
    raw_jobs = payload.get("jobs")
    if not isinstance(raw_jobs, list):
        return []
    normalized = [_normalize_direct_candidate(job) for job in raw_jobs if isinstance(job, Mapping)]
    return dedupe_jobs_by_normalized_url([job for job in normalized if job])[: max(1, int(max_jobs))]


def verify_and_prerank_direct_jobs(
    client: ResponseRequestClient,
    *,
    config: Mapping[str, Any],
    jobs: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not jobs:
        return []
    search_config = config.get("search") if isinstance(config.get("search"), Mapping) else {}
    direct_config = config.get("directJobDiscovery") if isinstance(config.get("directJobDiscovery"), Mapping) else {}
    model = str(direct_config.get("verifyModel") or direct_config.get("model") or search_config.get("model") or "").strip()
    if not model:
        return [dict(job) for job in jobs if isinstance(job, Mapping)]
    request = build_json_schema_request(
        model=model,
        input_payload=build_text_input_messages(
            "You live-check job pages and give rough candidate-fit scores. Return only structured JSON.",
            DIRECT_JOB_VERIFICATION_PROMPT.format(
                candidate_context_json=json.dumps(_candidate_context_payload(config), ensure_ascii=False, indent=2),
                jobs_json=json.dumps([_verification_payload(job) for job in jobs], ensure_ascii=False, indent=2),
            ),
        ),
        schema_name="direct_job_live_verification",
        schema=DIRECT_JOB_VERIFICATION_SCHEMA,
        use_web_search=True,
    )
    response = client.create(request)
    payload = parse_response_json(response, "Direct job live verification")
    raw_jobs = payload.get("jobs")
    if not isinstance(raw_jobs, list):
        return []
    originals_by_url = {normalize_job_url(job.get("url") or ""): dict(job) for job in jobs if isinstance(job, Mapping)}
    merged: list[dict[str, Any]] = []
    for item in raw_jobs:
        if not isinstance(item, Mapping):
            continue
        normalized_url = normalize_job_url(item.get("url") or item.get("finalUrl") or "")
        original = originals_by_url.get(normalized_url, {})
        merged_job = merge_job_item(original, _normalize_verified_job(item, config=config))
        if merged_job:
            merged.append(merged_job)
    return dedupe_jobs_by_normalized_url(merged)


def _normalize_direct_candidate(raw_job: Mapping[str, Any]) -> dict[str, Any]:
    url = normalize_job_url(raw_job.get("url") or "")
    title = str(raw_job.get("title") or "").strip()
    summary = str(raw_job.get("summary") or "").strip()
    if not has_job_signal(title=title, url=url, summary=summary):
        return {}
    company = str(raw_job.get("company") or "").strip()
    if not company:
        return {}
    return {
        "title": title,
        "company": company,
        "location": str(raw_job.get("location") or "").strip(),
        "url": url,
        "canonicalUrl": canonical_job_url({"url": url}) or url,
        "datePosted": str(raw_job.get("datePosted") or "").strip(),
        "dateFound": _now_iso(),
        "summary": summary,
        "availabilityHint": str(raw_job.get("sourceHint") or "").strip(),
        "source": "direct_job_discovery",
        "sourceType": "direct_job_discovery",
    }


def _normalize_verified_job(raw_job: Mapping[str, Any], *, config: Mapping[str, Any]) -> dict[str, Any]:
    final_url = normalize_job_url(raw_job.get("finalUrl") or "")
    if not final_url or not is_specific_job_detail_url(final_url):
        return {}
    if not bool(raw_job.get("isLiveJobPage")) or not bool(raw_job.get("hasApplyEntry")):
        return {}
    job = {
        "title": str(raw_job.get("title") or "").strip(),
        "company": str(raw_job.get("company") or "").strip(),
        "location": str(raw_job.get("location") or "").strip(),
        "url": final_url,
        "canonicalUrl": final_url,
        "datePosted": str(raw_job.get("datePosted") or "").strip(),
        "dateFound": _now_iso(),
        "summary": str(raw_job.get("summary") or "").strip(),
        "availabilityHint": str(raw_job.get("reason") or "").strip(),
        "applyUrl": normalize_job_url(raw_job.get("applyUrl") or ""),
        "source": "direct_job_discovery",
        "sourceType": "direct_job_discovery",
        "directJobVerification": {
            "isLiveJobPage": bool(raw_job.get("isLiveJobPage")),
            "hasApplyEntry": bool(raw_job.get("hasApplyEntry")),
            "fastFitScore": _clamp_score(raw_job.get("fastFitScore")),
            "reason": str(raw_job.get("reason") or "").strip(),
            "verifiedAt": _now_iso(),
        },
    }
    job["sourceQuality"] = infer_source_quality(job, config)
    job["regionTag"] = infer_region_tag(job)
    return job


def _score_direct_job(
    client: ResponseRequestClient,
    *,
    config: Mapping[str, Any],
    candidate_profile: Mapping[str, Any] | None,
    data_availability_note: str,
    search_run_id: int,
    job: Mapping[str, Any],
) -> dict[str, Any]:
    working_job = dict(job)
    try:
        analysis = JobAnalysisService.score_job_fit(
            client,
            config=config,
            candidate_profile=candidate_profile,
            job=working_job,
            data_availability_note=data_availability_note,
        )
        role_binding = JobAnalysisService.evaluate_target_roles_for_job(
            client,
            config=config,
            candidate_profile=candidate_profile,
            job=working_job,
            analysis=analysis,
        )
        analysis_payload = JobAnalysisService.prepare_analysis_for_storage(
            analysis,
            role_binding,
            config=config,
        )
        analysis_payload["postVerifySkipped"] = True
        analysis_payload["updatedAt"] = _now_iso()
        working_job["analysis"] = analysis_payload
        working_job["processingState"] = clear_work_unit_state()
    except Exception as exc:
        working_job["processingState"] = record_technical_failure(
            working_job.get("processingState"),
            run_id=search_run_id,
            reason=str(exc or "").strip() or "direct_job_scoring_failure",
        )
    return working_job


def _upsert_direct_job_companies(
    runtime_mirror,
    *,
    candidate_id: int,
    jobs: list[Mapping[str, Any]],
    discovered_at: str,
) -> int:
    if not jobs:
        return 0
    master = runtime_mirror.load_candidate_company_pool(candidate_id=candidate_id)
    incoming = [_company_candidate_from_direct_job(job, discovered_at=discovered_at) for job in jobs]
    incoming = [company for company in incoming if company]
    if not incoming:
        return 0
    merged, changed = merge_companies_into_master(master, incoming)
    reactivated_keys = {
        key
        for company in incoming
        for key in build_company_identity_keys(company)
        if key
    }
    for company in merged:
        if not isinstance(company, dict):
            continue
        if bool(company.get("inactive")):
            continue
        if not reactivated_keys.intersection(build_company_identity_keys(company)):
            continue
        company.pop("cooldownUntil", None)
        company.pop("cooldownAppliedAt", None)
        company.pop("sourceWorkState", None)
        company.pop("rankingWorkState", None)
    runtime_mirror.replace_candidate_company_pool(candidate_id=candidate_id, companies=merged)
    return max(changed, len(incoming))


def _company_candidate_from_direct_job(job: Mapping[str, Any], *, discovered_at: str) -> dict[str, Any]:
    company = str(job.get("company") or "").strip()
    url = normalize_job_url(job.get("url") or "")
    if not company or not url:
        return {}
    website = _company_website_from_job(job)
    score = _job_quality_score(job)
    return {
        "name": company,
        "website": website,
        "businessSummary": f"Directly discovered matching live role: {str(job.get('title') or '').strip()}",
        "tags": ["source:direct_job", "direct_job_discovery"],
        "discoverySources": ["direct_job_discovery"],
        "sourceEvidence": {
            "directJobDiscovery": {
                "lastSeenAt": discovered_at,
                "jobTitle": str(job.get("title") or "").strip(),
                "jobUrl": url,
                "score": score,
            }
        },
        "knownJobUrls": [url],
        "snapshotJobUrls": [url],
        "sampleJobUrls": [url],
        "lastGoodJobAt": discovered_at,
        "signalCount": 1,
        "snapshotComplete": False,
    }


def _company_website_from_job(job: Mapping[str, Any]) -> str:
    company_website = normalize_url(job.get("companyWebsite"))
    if company_website:
        return company_website
    url = normalize_job_url(job.get("url") or "")
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except Exception:
        return ""
    host = str(parts.netloc or "").strip().casefold()
    if not host:
        return ""
    if any(token in host for token in ("greenhouse.io", "lever.co", "smartrecruiters.com", "myworkdayjobs.com")):
        return ""
    return f"{parts.scheme or 'https'}://{host}"


def _load_historical_jobs(runtime_mirror, candidate_id: int) -> list[dict[str, Any]]:
    loader = getattr(runtime_mirror, "load_candidate_bucket_jobs_merged", None)
    if not callable(loader):
        return []
    jobs: list[dict[str, Any]] = []
    for bucket in ("found", "all", "recommended"):
        try:
            jobs.extend(loader(candidate_id=candidate_id, job_bucket=bucket))
        except Exception:
            continue
    return [dict(item) for item in jobs if isinstance(item, Mapping)]


def _build_seen_job_keys(jobs: list[Mapping[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for item in jobs:
        if not isinstance(item, Mapping):
            continue
        job = dict(item)
        key = job_item_key(job) or job_identity_key(job)
        if key:
            keys.add(key.casefold())
        normalized_url = normalize_job_url(job.get("url") or job.get("canonicalUrl") or "")
        if normalized_url:
            keys.add(normalized_url.casefold())
        identity = _title_company_location_key(job)
        if identity:
            keys.add(identity)
    return keys


def _filter_new_jobs(
    jobs: list[Mapping[str, Any]],
    seen_keys: set[str],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    new_jobs: list[dict[str, Any]] = []
    skipped = 0
    local_seen = set(seen_keys)
    for raw_job in jobs:
        job = dict(raw_job)
        if _job_seen(job, local_seen):
            skipped += 1
            continue
        for key in _job_keys(job):
            local_seen.add(key)
        new_jobs.append(job)
        if len(new_jobs) >= max(1, int(limit)):
            break
    return new_jobs, skipped


def _job_seen(job: Mapping[str, Any], seen_keys: set[str]) -> bool:
    return any(key in seen_keys for key in _job_keys(job))


def _job_keys(job: Mapping[str, Any]) -> list[str]:
    keys: list[str] = []
    key = job_item_key(dict(job)) or job_identity_key(dict(job))
    if key:
        keys.append(key.casefold())
    normalized_url = normalize_job_url(job.get("canonicalUrl") or job.get("url") or "")
    if normalized_url:
        keys.append(normalized_url.casefold())
    identity = _title_company_location_key(job)
    if identity:
        keys.append(identity)
    return merge_unique_strings(keys)


def _title_company_location_key(job: Mapping[str, Any]) -> str:
    title = " ".join(str(job.get("title") or "").strip().casefold().split())
    company = " ".join(str(job.get("company") or "").strip().casefold().split())
    location = " ".join(str(job.get("location") or "").strip().casefold().split())
    if not title or not company:
        return ""
    return f"title-company-location:{title}|{company}|{location}"


def _direct_job_is_live(job: Mapping[str, Any]) -> bool:
    verification = job.get("directJobVerification")
    if not isinstance(verification, Mapping):
        return False
    if not bool(verification.get("isLiveJobPage")):
        return False
    if not bool(verification.get("hasApplyEntry")):
        return False
    return has_job_signal(
        title=job.get("title") or "",
        url=job.get("url") or "",
        summary=job.get("summary") or "",
    )


def _job_is_recommended(job: Mapping[str, Any]) -> bool:
    analysis = job.get("analysis")
    return isinstance(analysis, Mapping) and bool(analysis.get("recommend"))


def _job_quality_score(job: Mapping[str, Any]) -> int:
    analysis = job.get("analysis")
    if not isinstance(analysis, Mapping):
        return 0
    return _clamp_score(analysis.get("overallScore") or analysis.get("matchScore") or analysis.get("score"))


def _analysis_completed(analysis: object) -> bool:
    return analysis_completed(analysis)


def _candidate_context_payload(config: Mapping[str, Any]) -> dict[str, Any]:
    candidate = config.get("candidate") if isinstance(config.get("candidate"), Mapping) else {}
    semantic = candidate.get("semanticProfile") if isinstance(candidate.get("semanticProfile"), Mapping) else {}
    target_roles: list[str] = []
    raw_roles = candidate.get("targetRoles")
    if isinstance(raw_roles, list):
        for role in raw_roles:
            if not isinstance(role, Mapping):
                continue
            text = str(
                role.get("displayName")
                or role.get("targetRoleText")
                or role.get("nameEn")
                or role.get("nameZh")
                or ""
            ).strip()
            if text and text not in target_roles:
                target_roles.append(text)
    return {
        "summary": _truncate_text(semantic.get("summary"), 600),
        "careerAndEducationHistory": _truncate_text(
            semantic.get("career_and_education_history"),
            600,
        ),
        "targetRoles": target_roles[:8],
        "scopeProfiles": _trim_text_list(candidate.get("scopeProfiles"), limit=8),
        "locationPreference": str(candidate.get("locationPreference") or "").strip(),
        "jobFitCoreTerms": _trim_text_list(semantic.get("job_fit_core_terms"), limit=14),
        "jobFitSupportTerms": _trim_text_list(semantic.get("job_fit_support_terms"), limit=12),
        "companyDiscoveryPrimaryAnchors": _trim_text_list(
            semantic.get("company_discovery_primary_anchors"),
            limit=10,
        ),
        "companyDiscoverySecondaryAnchors": _trim_text_list(
            semantic.get("company_discovery_secondary_anchors"),
            limit=8,
        ),
        "avoidBusinessAreas": _trim_text_list(semantic.get("avoid_business_areas"), limit=8),
    }


def _verification_payload(job: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "title": str(job.get("title") or "").strip(),
        "company": str(job.get("company") or "").strip(),
        "location": str(job.get("location") or "").strip(),
        "url": normalize_job_url(job.get("url") or ""),
        "datePosted": str(job.get("datePosted") or "").strip(),
        "summary": _truncate_text(job.get("summary"), 500),
    }


def _trim_text_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= max(0, int(limit)):
            break
    return out


def _truncate_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, int(limit) - 1)].rstrip() + "..."


def _clamp_score(value: object) -> int:
    try:
        if isinstance(value, bool):
            return 0
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return 0


def _to_number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


__all__ = [
    "discover_direct_jobs_for_candidate",
    "run_direct_job_discovery_stage_db",
    "verify_and_prerank_direct_jobs",
]
