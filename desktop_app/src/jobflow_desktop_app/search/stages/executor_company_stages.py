from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..orchestration import runtime_config_builder
from ..companies.discovery import auto_discover_companies_in_pool
from ..companies.selection import select_companies_for_run
from ..companies.sources import collect_supported_company_source_jobs
from .company_stage_support import build_company_sources_stage_artifacts
from .executor import PythonStageRunResult
from .executor_common import _config_mapping, _relay_progress, _tail_lines

if TYPE_CHECKING:
    from ..analysis.service import ResponseRequestClient


def run_company_discovery_stage_db(
    *,
    runtime_mirror,
    search_run_id: int,
    candidate_id: int,
    config: dict,
    client_instance: "ResponseRequestClient",
    query_budget: int | None,
    max_new_companies: int | None,
    progress_callback: Callable[[str], None] | None,
) -> PythonStageRunResult:
    del search_run_id
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    try:
        result = auto_discover_companies_in_pool(
            client_instance,
            config=config,
            companies=runtime_mirror.load_candidate_company_pool(
                candidate_id=candidate_id,
            ),
            query_stats={},
            query_budget=query_budget,
            max_new_companies=max_new_companies,
            progress_callback=lambda line: _relay_progress(line, stdout_lines, progress_callback),
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
        f"Python company discovery stage completed. Added {int(result.get('added') or 0)} company(s); "
        f"pool now has {int(result.get('total') or 0)} company(s)."
    )
    stdout_lines.append(message)
    return PythonStageRunResult(
        success=True,
        exit_code=0,
        message=message,
        stdout_tail=_tail_lines(stdout_lines),
        stderr_tail=_tail_lines(stderr_lines),
    )


def run_company_selection_stage_db(
    *,
    runtime_mirror,
    candidate_id: int,
    config: dict,
    max_companies: int | None,
    progress_callback: Callable[[str], None] | None,
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
        selection = select_companies_for_run(
            config=config,
            companies=companies,
            max_companies=runtime_config_builder.resolve_effective_max_companies(
                requested_max_companies=max_companies,
                runtime_config=config,
            ),
        )
        selected_companies = [
            dict(item)
            for item in selection
            if isinstance(item, dict)
        ]
        _relay_progress(
            f"Python company selection prepared {len(selected_companies)} company(s) for ATS fetching.",
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
        result = collect_supported_company_source_jobs(
            resolved_selected_companies,
            config=config,
            timeout_seconds=timeout_seconds,
            progress_callback=lambda line: _relay_progress(line, stdout_lines, progress_callback),
            client=client_instance,
        )
        artifacts = build_company_sources_stage_artifacts(
            master_companies=runtime_mirror.load_candidate_company_pool(
                candidate_id=candidate_id,
            ),
            existing_jobs=runtime_mirror.load_run_bucket_jobs(
                search_run_id=search_run_id,
                job_bucket="all",
            ),
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
        payload={"remainingSelectedCompanies": []},
    )


__all__ = [
    "run_company_discovery_stage_db",
    "run_company_selection_stage_db",
    "run_company_sources_stage_db",
]
