from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from . import search_results_links


def parse_date_found(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def job_key(job: Any) -> str:
    url = str(getattr(job, "url", "") or "").strip()
    if url:
        return url.casefold()
    return (
        f"{getattr(job, 'title', '')}|{getattr(job, 'company', '')}|{getattr(job, 'date_found', '')}"
    ).casefold()


def visible_jobs(jobs: Iterable[Any], hidden_job_keys: set[str] | None = None) -> list[Any]:
    hidden = hidden_job_keys or set()
    sorted_jobs = sorted(
        jobs,
        key=lambda item: parse_date_found(str(getattr(item, "date_found", "") or "")),
        reverse=True,
    )
    return [item for item in sorted_jobs if job_key(item) not in hidden]


def job_render_signature(job: Any) -> tuple[object, ...]:
    detail_url, final_url, link_status = search_results_links.job_link_details(job)
    return (
        str(getattr(job, "url", "") or "").strip().casefold(),
        detail_url.casefold(),
        final_url.casefold(),
        link_status.casefold(),
        str(getattr(job, "title", "") or "").strip(),
        str(getattr(job, "company", "") or "").strip(),
        str(getattr(job, "location", "") or "").strip(),
        str(getattr(job, "date_found", "") or "").strip(),
        getattr(job, "match_score", None),
        getattr(job, "overall_match_score", None),
        bool(getattr(job, "recommend", False)),
        str(getattr(job, "fit_level_cn", "") or "").strip(),
        str(getattr(job, "fit_track", "") or "").strip(),
        str(getattr(job, "adjacent_direction_cn", "") or "").strip(),
        str(getattr(job, "bound_target_role_id", "") or "").strip(),
        str(getattr(job, "bound_target_role_name_zh", "") or "").strip(),
        str(getattr(job, "bound_target_role_name_en", "") or "").strip(),
        str(getattr(job, "bound_target_role_display_name", "") or "").strip(),
        str(getattr(job, "bound_target_role_text", "") or "").strip(),
        getattr(job, "bound_target_role_score", None),
    )


def jobs_signature(jobs: Iterable[Any]) -> tuple[tuple[object, ...], ...]:
    return tuple(job_render_signature(job) for job in jobs)


__all__ = [
    "job_key",
    "job_render_signature",
    "jobs_signature",
    "parse_date_found",
    "visible_jobs",
]
