from __future__ import annotations

from ...ai.role_recommendations import role_name_query_lines
from ...db.repositories.profiles import SearchProfileRecord
from ...search.companies.source_diagnostics import (
    select_recent_company_source_diagnostic_summary,
)
from . import search_results_status
from ..widgets.common import _t


def set_no_candidate_status(page) -> None:
    main_message = _t(
        page.ui_language,
        "请先返回选择页，选择一个求职者后再开始搜索。",
        "Go back to candidate selection and choose a candidate first.",
    )
    detail_message = _t(
        page.ui_language,
        "当前还没有选中求职者，所以“开始搜索”暂时不可用。",
        "No candidate is selected yet, so Start Search is unavailable.",
    )
    page._set_results_main_status(main_message)
    page._set_results_progress_detail_with_level(detail_message, alert=True)


def set_ready_status(page, candidate_name: str | None = None) -> None:
    page._set_results_main_status(
        page._candidate_status_text(
            "点击“开始搜索”后，系统会继续抓取，并把新的岗位结果按最新时间加入列表。",
            "Click 'Start Search' to continue discovery and add new jobs to the list in newest-first order.",
            candidate_name=candidate_name,
        )
    )
    page._set_results_progress_detail("")


def set_loaded_results_status(page, visible_count: int, pending_count: int, stats=None) -> None:
    if visible_count > 0 and pending_count > 0:
        message = page._candidate_status_text(
            f"已加载最近一次运行结果；另有 {pending_count} 条上次未补完的岗位，下次开始搜索时会优先继续处理。",
            f"Loaded the latest run results; {pending_count} unfinished job(s) from the last run will be resumed first the next time you start search.",
        )
    elif visible_count > 0:
        message = page._candidate_status_text(
            "已加载最近一次运行结果。",
            "Loaded the latest run results.",
        )
    elif pending_count > 0:
        message = page._candidate_status_text(
            f"当前还没有可展示结果；另有 {pending_count} 条上次未补完的岗位，下次开始搜索时会优先继续处理。",
            f"There are no displayable results yet; {pending_count} unfinished job(s) from the last run will be resumed first the next time you start search.",
        )
    else:
        set_ready_status(page)
        return
    page._set_results_main_status(message)
    if stats is None:
        page._set_results_progress_detail("")
        return
    page._set_results_progress_detail(
        search_results_status.search_completion_detail(
            page.ui_language,
            discovered_job_count=getattr(stats, "main_discovered_job_count", 0),
            scored_job_count=getattr(stats, "main_scored_job_count", 0),
            recommended_job_count=getattr(stats, "recommended_job_count", 0),
            pending_job_count=getattr(stats, "main_pending_analysis_count", 0),
            candidate_company_pool_count=getattr(stats, "candidate_company_pool_count", 0),
        )
    )


def refresh_results_stats_label(page) -> None:
    candidate_id = page.current_candidate_id
    if candidate_id is None:
        text = _t(
            page.ui_language,
            "内部统计：未选择求职者。",
            "Internal stats: no candidate selected.",
        )
    else:
        try:
            stats = page.runner.load_search_stats(candidate_id)
        except Exception as exc:
            text = _t(
                page.ui_language,
                f"内部统计读取失败：{exc}",
                f"Failed to read internal stats: {exc}",
            )
        else:
            summary = _t(
                page.ui_language,
                (
                    f"内部统计：当前候选公司池 {stats.candidate_company_pool_count} 家。\n"
                    f"主流程已发现 {stats.main_discovered_job_count} 条，"
                    f"已评分 {stats.main_scored_job_count} 条，"
                    f"待补完 {stats.main_pending_analysis_count} 条。"
                ),
                (
                    f"Internal stats: current candidate pool {stats.candidate_company_pool_count}.\n"
                    f"company-first discovered {stats.main_discovered_job_count}, "
                    f"scored {stats.main_scored_job_count}, "
                    f"pending completion {stats.main_pending_analysis_count}."
                ),
            )
            if (
                stats.main_discovered_job_count == 0
                and stats.main_pending_analysis_count == 0
            ):
                summary = summary + _t(
                    page.ui_language,
                    " 当前运行目录里还没有已落盘的岗位结果。",
                    " No persisted job results in the current run directory yet.",
                )
            diagnosis_summary = ""
            runtime_mirror = getattr(page.runner, "runtime_mirror", None)
            if runtime_mirror is not None:
                try:
                    companies = runtime_mirror.load_candidate_company_pool(
                        candidate_id=int(candidate_id),
                    )
                except Exception:
                    companies = []
                diagnosis_summary = select_recent_company_source_diagnostic_summary(
                    companies,
                    ui_language=page.ui_language,
                )
            if diagnosis_summary:
                summary = summary + "\n" + _t(
                    page.ui_language,
                    f"最近公司诊断：{diagnosis_summary}",
                    f"Latest company diagnosis: {diagnosis_summary}",
                )
            text = summary
    if page.results_stats_label.text() != text:
        page.results_stats_label.setText(text)


def build_target_role_candidates(profiles: list[SearchProfileRecord]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for profile in profiles:
        if not profile.is_active:
            continue
        for raw in role_name_query_lines(profile.role_name_i18n, fallback_name=profile.name):
            text = str(raw or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(text)
        for raw in (profile.target_role,):
            text = str(raw or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(text)
    return candidates


def reload_existing_results(page, candidate_id: int) -> None:
    load_live_jobs = getattr(page.runner, "load_live_jobs", None)
    jobs = load_live_jobs(candidate_id) if callable(load_live_jobs) else []
    if not jobs:
        jobs = page.runner.load_recommended_jobs(candidate_id)
    visible_count = page._render_jobs(jobs)
    try:
        stats = page.runner.load_search_stats(candidate_id)
    except Exception:
        stats = None
    pending_count = max(0, int(getattr(stats, "main_pending_analysis_count", 0) or 0))
    set_loaded_results_status(page, visible_count, pending_count, stats)
    refresh_results_stats_label(page)
    page._live_results_last_count = visible_count


def apply_non_running_candidate(page, candidate) -> None:
    page._stop_live_results_updates()
    page.table.setRowCount(0)
    page._live_results_signature = ()
    session = page._search_session
    running_search = page._is_search_running()
    queued_restart = bool(getattr(session, "queued_restart", False))
    stop_requested = bool(getattr(session, "stop_requested", False))
    if candidate is None or candidate.candidate_id is None:
        page._current_candidate = None
        page.target_role_candidates = []
        page.status_by_job_key = {}
        page.hidden_job_keys = set()
        page._set_search_button_running(False)
        page.refresh_button.setEnabled(False)
        page.delete_button.setEnabled(False)
        page.search_duration_combo.setEnabled(False)
        if running_search or queued_restart or stop_requested:
            page._set_results_main_status(
                _t(
                    page.ui_language,
                    "当前没有选中求职者，但后台搜索仍在运行。请先回到对应求职者，或等待后台任务完成。",
                    "No candidate is selected right now, but a background search is still running. Switch back to that candidate or wait for the background task to finish.",
                )
            )
            page._set_results_progress_detail(
                _t(
                    page.ui_language,
                    "后台任务仍在运行，当前只是不再显示这位求职者的结果。",
                    "The background task is still running; this view is just detached from that candidate.",
                )
            )
        else:
            set_no_candidate_status(page)
            page._reset_search_runtime_state()
        page.refresh_button.setToolTip(page.results_progress_label.text())
        page.search_duration_combo.setToolTip(page.results_progress_label.text())
        refresh_results_stats_label(page)
        return

    page._current_candidate = candidate
    profiles = page.context.profiles.list_for_candidate(candidate.candidate_id)
    page.target_role_candidates = build_target_role_candidates(profiles)
    page._load_review_state(candidate.candidate_id)
    page.delete_button.setEnabled(True)
    if not page._is_search_running(candidate.candidate_id):
        page._set_search_countdown_seconds(0)
        page._search_countdown_timer.stop()
    else:
        page._refresh_search_countdown()
        page._search_countdown_timer.start()
    set_ready_status(page, candidate.name)
    refresh_results_stats_label(page)
    reload_existing_results(page, candidate.candidate_id)
    page._apply_search_prerequisite_state(profiles=profiles)


__all__ = [
    "apply_non_running_candidate",
    "build_target_role_candidates",
    "refresh_results_stats_label",
    "reload_existing_results",
    "set_loaded_results_status",
    "set_no_candidate_status",
    "set_ready_status",
]
