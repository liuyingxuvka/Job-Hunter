from __future__ import annotations

from ..widgets.common import _t
from . import search_results_live_state
from . import search_results_status


def visible_jobs(page, jobs):
    return search_results_live_state.visible_jobs(
        jobs,
        page.hidden_job_keys,
    )


def job_render_signature(job):
    return search_results_live_state.job_render_signature(job)


def jobs_signature(jobs):
    return search_results_live_state.jobs_signature(jobs)


def sync_live_results_signature(page) -> None:
    if page.current_candidate_id is None:
        page._live_results_signature = ()
        return
    jobs = page.runner.load_live_jobs(page.current_candidate_id)
    if not jobs:
        jobs = page.runner.load_recommended_jobs(page.current_candidate_id)
    page._live_results_signature = jobs_signature(visible_jobs(page, jobs))


def main_pending_analysis_count(page, candidate_id: int | None = None) -> int:
    target_candidate_id = candidate_id if candidate_id is not None else page.current_candidate_id
    if target_candidate_id is None:
        return 0
    try:
        stats = page.runner.load_search_stats(int(target_candidate_id))
    except Exception:
        return 0
    return max(0, int(getattr(stats, "main_pending_analysis_count", 0) or 0))


def search_progress_text(page, candidate_id: int | None) -> tuple[str, str]:
    if candidate_id is None:
        return "", ""
    try:
        progress = page.runner.load_search_progress(int(candidate_id))
    except Exception:
        return "", ""
    if str(getattr(progress, "status", "") or "").strip().lower() != "running":
        return "", ""

    stage = str(getattr(progress, "stage", "") or "").strip().lower()
    stage_label = search_results_status.progress_stage_label(page.ui_language, stage)

    session = getattr(page, "_search_session", None)
    stop_requested = bool(getattr(session, "stop_requested", False))
    queued_restart = bool(getattr(session, "queued_restart", False))
    queued_duration_label = str(getattr(session, "queued_restart_duration_label", "")).strip()
    return search_results_status.search_progress_text(
        page.ui_language,
        progress,
        stop_requested=stop_requested,
        queued_restart=queued_restart,
        queued_duration_label=queued_duration_label,
        selected_duration_label=page._selected_search_duration_label(),
    )


def start_live_results_updates(page, candidate_id: int) -> None:
    del candidate_id
    page._live_results_last_count = -1
    page._live_results_detail_text = ""
    page._live_results_progress_signature = ("", "")
    refresh_live_results(page)
    page._live_results_timer.start()


def stop_live_results_updates(page) -> None:
    page._live_results_timer.stop()
    page._live_results_detail_text = ""
    page._live_results_progress_signature = ("", "")


def refresh_live_results(page) -> None:
    candidate_id = page.current_candidate_id
    session = getattr(page, "_search_session", None)
    owner_candidate_id = getattr(session, "owner_candidate_id", None)
    if (
        candidate_id is None
        or owner_candidate_id is None
        or candidate_id != owner_candidate_id
        or not page._is_search_running(candidate_id)
    ):
        return
    page._refresh_results_stats_label()
    progress_detail_text, progress_dialog_text = search_progress_text(page, candidate_id)
    progress_signature = (progress_detail_text, progress_dialog_text)
    if progress_signature != getattr(page, "_live_results_progress_signature", ("", "")):
        if progress_dialog_text:
            page._set_busy_task_message(progress_dialog_text)
        page._live_results_progress_signature = progress_signature
    jobs = page.runner.load_live_jobs(candidate_id)
    visible_jobs_list = visible_jobs(page, jobs)
    if visible_jobs_list:
        signature = jobs_signature(visible_jobs_list)
        visible_count = len(visible_jobs_list)
        if signature != page._live_results_signature:
            visible_count = page._render_visible_jobs(visible_jobs_list)
            page._live_results_last_count = visible_count
        detail_text = _t(
            page.ui_language,
            f"后台进度：当前临时结果 {visible_count} 条。",
            f"Background progress: {visible_count} interim result(s).",
        )
    elif page._live_results_last_count > 0:
        detail_text = progress_detail_text
    else:
        detail_text = progress_detail_text or _t(
            page.ui_language,
            "后台进度：暂时还没有新增岗位，系统通常仍在抓取、分析或收尾。",
            "Background progress: no new jobs yet; the system is usually still collecting, analyzing, or finishing.",
        )
    if detail_text != getattr(page, "_live_results_detail_text", ""):
        page._set_results_progress_detail(detail_text)
        page._live_results_detail_text = detail_text


__all__ = [
    "job_render_signature",
    "jobs_signature",
    "main_pending_analysis_count",
    "refresh_live_results",
    "search_progress_text",
    "start_live_results_updates",
    "stop_live_results_updates",
    "sync_live_results_signature",
    "visible_jobs",
]
