from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..orchestration import runtime_config_builder
from ..companies.ai_ranking import score_companies_for_candidate
from ..companies.discovery import (
    auto_discover_companies_in_pool,
)
from ..companies.ranking_thresholds import COMPANY_FIT_MIN_SCORE
from ..companies.selection import (
    company_record_key,
    select_companies_for_run,
    unresolved_company_ranking_count,
)
from ..companies.sources import CompanySourcesFetchResult, collect_supported_company_source_jobs
from .company_stage_support import build_company_sources_stage_artifacts
from ..state.work_unit_state import is_abandoned, suspend_for_current_run
from .executor import PythonStageRunResult
from .executor_common import _config_mapping, _relay_progress, _tail_lines

if TYPE_CHECKING:
    from ..analysis.service import ResponseRequestClient


def _count_qualified_new_companies(
    companies: list[dict],
    new_companies: list[dict],
) -> tuple[int, int]:
    company_scores: dict[str, dict] = {}
    for company in companies:
        if not isinstance(company, dict):
            continue
        company_key = company_record_key(company)
        if company_key:
            company_scores[company_key] = company
    qualified = 0
    pending = 0
    for company in new_companies:
        if not isinstance(company, dict):
            continue
        company_key = company_record_key(company)
        if not company_key:
            continue
        ranked = company_scores.get(company_key) or company
        if float(ranked.get("aiCompanyFitScore") or 0) >= COMPANY_FIT_MIN_SCORE:
            qualified += 1
            continue
        if ranked.get("aiCompanyFitScore") is None and not is_abandoned(ranked.get("rankingWorkState")):
            pending += 1
    return qualified, pending


def run_company_discovery_stage_db(
    *,
    runtime_mirror,
    search_run_id: int,
    candidate_id: int,
    config: dict,
    client_instance: "ResponseRequestClient",
    progress_callback: Callable[[str], None] | None,
) -> PythonStageRunResult:
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    try:
        company_discovery = _config_mapping(config, "companyDiscovery")
        company_discovery_input = (
            dict(company_discovery.get("companyDiscoveryInput"))
            if isinstance(company_discovery.get("companyDiscoveryInput"), dict)
            else {}
        )
        has_company_discovery_input = any(
            bool(str(value).strip()) if not isinstance(value, (list, dict)) else bool(value)
            for value in company_discovery_input.values()
        )
        initial_companies = runtime_mirror.load_candidate_company_pool(
            candidate_id=candidate_id,
        )
        result: dict = {"added": 0, "total": len(initial_companies), "companies": initial_companies}
        qualified_new_companies = 0
        pending_new_companies = 0
        step_result = auto_discover_companies_in_pool(
            client_instance,
            config=config,
            companies=initial_companies,
            progress_callback=lambda line: _relay_progress(line, stdout_lines, progress_callback),
        )
        result = step_result
        companies_after_discovery = [
            dict(item)
            for item in step_result.get("companies", [])
            if isinstance(item, dict)
        ]
        runtime_mirror.replace_candidate_company_pool(
            candidate_id=candidate_id,
            companies=companies_after_discovery,
        )
        if step_result.get("added"):
            scored_companies = score_companies_for_candidate(
                client_instance,
                config=config,
                companies=companies_after_discovery,
                current_run_id=search_run_id,
            )
            if scored_companies != companies_after_discovery:
                runtime_mirror.replace_candidate_company_pool(
                    candidate_id=candidate_id,
                    companies=[dict(item) for item in scored_companies if isinstance(item, dict)],
                )
            result["companies"] = [
                dict(item)
                for item in scored_companies
                if isinstance(item, dict)
            ]
            qualified_new_companies, pending_new_companies = _count_qualified_new_companies(
                result["companies"],
                [
                    dict(item)
                    for item in step_result.get("newCompanies", [])
                    if isinstance(item, dict)
                ],
            )
        _relay_progress(
            "Python direct company discovery: "
            f"added={int(step_result.get('added') or 0)} "
            f"qualifiedNew={qualified_new_companies} "
            f"pendingNew={pending_new_companies}",
            stdout_lines,
            progress_callback,
        )
        runtime_mirror.replace_candidate_company_pool(
            candidate_id=candidate_id,
            companies=[
                dict(item)
                for item in result.get("companies", [])
                if isinstance(item, dict)
            ],
        )
    except Exception as exc:
        message = f"Python company discovery stage failed: {exc}"
        stderr_lines.append(message)
        return PythonStageRunResult(
            success=False,
            exit_code=-1,
            message=message,
            stdout_tail=_tail_lines(stdout_lines),
            stderr_tail=_tail_lines(stderr_lines),
        )

    message = (
        "Python company discovery stage completed. "
        f"Added {int(result.get('added') or 0)} company(s); "
        f"qualifiedNew={qualified_new_companies}; pendingNew={pending_new_companies}; "
        f"pool now has {int(result.get('total') or 0)} company(s)."
    )
    payload = {
        "noQualifiedNewCompanies": bool(
            has_company_discovery_input
            and qualified_new_companies <= 0
            and pending_new_companies <= 0
        ),
    }
    stdout_lines.append(message)
    return PythonStageRunResult(
        success=True,
        exit_code=0,
        message=message,
        stdout_tail=_tail_lines(stdout_lines),
        stderr_tail=_tail_lines(stderr_lines),
        payload=payload,
    )


def run_company_selection_stage_db(
    *,
    runtime_mirror,
    search_run_id: int | None = None,
    candidate_id: int,
    config: dict,
    max_companies: int | None,
    progress_callback: Callable[[str], None] | None,
    client_instance: "ResponseRequestClient | None" = None,
) -> PythonStageRunResult:
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    companies = runtime_mirror.load_candidate_company_pool(
        candidate_id=candidate_id,
    )
    if not companies:
        return PythonStageRunResult(
            success=True,
            exit_code=0,
            message="Python company selection stage skipped because the company pool is empty.",
            stdout_tail="",
            stderr_tail="",
            payload={"selectedCompanies": []},
        )

    try:
        effective_max_companies = runtime_config_builder.resolve_effective_max_companies(
            requested_max_companies=max_companies,
            runtime_config=config,
        )
        unresolved_rankings = unresolved_company_ranking_count(
            companies,
            current_run_id=search_run_id,
        )
        if unresolved_rankings > 0:
            scored_companies = score_companies_for_candidate(
                client_instance,
                config=config,
                companies=companies,
                current_run_id=search_run_id,
            )
            if scored_companies != companies:
                runtime_mirror.replace_candidate_company_pool(
                    candidate_id=candidate_id,
                    companies=[dict(item) for item in scored_companies if isinstance(item, dict)],
                )
            companies = scored_companies
        selection = select_companies_for_run(
            companies=companies,
            max_companies=effective_max_companies,
            current_run_id=search_run_id,
        )
        selected_companies = [
            dict(item)
            for item in selection
            if isinstance(item, dict)
        ]
        _relay_progress(
            f"Python company selection prepared {len(selected_companies)} company(s) for the sources stage.",
            stdout_lines,
            progress_callback,
        )
    except Exception as exc:
        message = f"Python company selection stage failed: {exc}"
        stderr_lines.append(message)
        return PythonStageRunResult(
            success=False,
            exit_code=-1,
            message=message,
            stdout_tail=_tail_lines(stdout_lines),
            stderr_tail=_tail_lines(stderr_lines),
        )

    message = (
        f"Python company selection stage completed. Selected {len(selected_companies)} company(s) "
        f"from {len(companies)} available company(s)."
    )
    stdout_lines.append(message)
    return PythonStageRunResult(
        success=True,
        exit_code=0,
        message=message,
        stdout_tail=_tail_lines(stdout_lines),
        stderr_tail=_tail_lines(stderr_lines),
        payload={"selectedCompanies": selected_companies},
    )


def run_company_sources_stage_db(
    *,
    runtime_mirror,
    search_run_id: int,
    candidate_id: int,
    config: dict,
    selected_companies: list[dict] | None,
    client_instance: "ResponseRequestClient | None",
    timeout_seconds: int | None,
    progress_callback: Callable[[str], None] | None,
) -> PythonStageRunResult:
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    resolved_selected_companies = [
        dict(item) for item in selected_companies if isinstance(item, dict)
    ] if isinstance(selected_companies, list) else []
    if not resolved_selected_companies:
        return PythonStageRunResult(
            success=True,
            exit_code=0,
            message="Python company sources stage skipped because no selected companies are available.",
            stdout_tail="",
            stderr_tail="",
            payload={"remainingSelectedCompanies": []},
        )

    try:
        existing_jobs = runtime_mirror.load_run_bucket_jobs(
            search_run_id=search_run_id,
            job_bucket="all",
        )
        result = collect_supported_company_source_jobs(
            resolved_selected_companies,
            config=config,
            existing_jobs=existing_jobs,
            timeout_seconds=timeout_seconds,
            search_run_id=search_run_id,
            progress_callback=lambda line: _relay_progress(line, stdout_lines, progress_callback),
            client=client_instance,
        )
        deferred_companies = [
            _suspend_company_for_current_run(dict(item), search_run_id)
            for item in result.remaining_companies
            if isinstance(item, dict)
        ]
        result = CompanySourcesFetchResult(
            jobs=result.jobs,
            processed_companies=result.processed_companies,
            remaining_companies=deferred_companies,
            jobs_found_count=result.jobs_found_count,
            companies_handled_count=result.companies_handled_count,
        )
        artifacts = build_company_sources_stage_artifacts(
            master_companies=runtime_mirror.load_candidate_company_pool(
                candidate_id=candidate_id,
            ),
            existing_jobs=existing_jobs,
            fetch_result=result,
            config=config,
        )
        runtime_mirror.commit_company_sources_round(
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            all_jobs=artifacts.all_jobs,
            found_jobs=artifacts.found_jobs,
            candidate_companies=artifacts.candidate_companies,
        )
    except Exception as exc:
        message = f"Python company sources stage failed: {exc}"
        stderr_lines.append(message)
        return PythonStageRunResult(
            success=False,
            exit_code=-1,
            message=message,
            stdout_tail=_tail_lines(stdout_lines),
            stderr_tail=_tail_lines(stderr_lines),
        )

    message = (
        f"Python company sources stage handled {result.companies_handled_count} company(s), "
        f"deferred {len(artifacts.deferred_companies)} company(s) back to the candidate pool, "
        f"and queued {len(result.jobs)} job(s)."
    )
    stdout_lines.append(message)
    return PythonStageRunResult(
        success=True,
        exit_code=0,
        message=message,
        stdout_tail=_tail_lines(stdout_lines),
        stderr_tail=_tail_lines(stderr_lines),
        payload={
            "remainingSelectedCompanies": [
                dict(item)
                for item in artifacts.deferred_companies
                if isinstance(item, dict)
            ]
        },
    )


def _suspend_company_for_current_run(
    company: dict,
    run_id: int | None,
) -> dict:
    company["sourceWorkState"] = suspend_for_current_run(
        company.get("sourceWorkState"),
        run_id=run_id,
        reason="source_stage_deferred",
    )
    return company


__all__ = [
    "run_company_discovery_stage_db",
    "run_company_selection_stage_db",
    "run_company_sources_stage_db",
]
