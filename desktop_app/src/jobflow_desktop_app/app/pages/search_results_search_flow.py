from __future__ import annotations

import threading
import time
from typing import Any

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QMessageBox

from ..widgets.common import _t
from . import search_results_runtime_state
from . import search_results_status


def _is_no_qualified_company_stop(details: object) -> bool:
    if not isinstance(details, dict):
        return False
    return str(details.get("stopReason") or "").strip() == "no_qualified_new_companies"


def _load_cumulative_table_jobs(page, candidate_id: int) -> list:
    return page.runner.load_recommended_jobs(int(candidate_id))


def toggle_search(page) -> None:
    session = page._search_session
    if page._is_search_running(page.current_candidate_id):
        if session.stop_requested:
            if session.queued_restart:
                page._cancel_queued_search_restart()
            else:
                page._queue_search_restart()
            return
        page._stop_search()
        return
    if page._is_search_running():
        QMessageBox.information(
            page,
            _t(page.ui_language, "岗位搜索结果", "Search Results"),
            page._search_prerequisite_issue() or _t(
                page.ui_language,
                "当前还有另一位求职者的岗位搜索正在运行，请先切回那位求职者查看状态。",
                "Another candidate still has a job search running. Switch back to that candidate to view its status first.",
            ),
        )
        page._apply_search_prerequisite_state()
        return
    page._run_search()


def stop_search(page) -> None:
    session = page._search_session
    cancel_event = session.cancel_event
    running_thread = session.worker_thread
    if (
        cancel_event is None
        or not isinstance(running_thread, QThread)
        or not running_thread.isRunning()
        or session.owner_candidate_id != page.current_candidate_id
    ):
        page._set_search_button_running(False)
        page._apply_search_prerequisite_state()
        return
    if cancel_event.is_set():
        if session.queued_for(page.current_candidate_id):
            page._set_search_button_running(False, queued=True)
            page.search_duration_combo.setEnabled(False)
        else:
            page._set_search_button_running(False)
            page.search_duration_combo.setEnabled(True)
        return
    cancel_event.set()
    search_results_runtime_state.cancel_running_searches_for_candidate(
        page,
        page.current_candidate_id,
        message="Search cancelled by the user.",
        last_event="User clicked Stop Search in the desktop app.",
    )
    search_results_runtime_state.request_search_stop(page)
    page._clear_queued_search_restart()
    page._reset_search_runtime_state()
    page._set_search_button_running(False)
    page.search_duration_combo.setEnabled(True)
    page._set_stopped_status(page.current_candidate_name)


def queue_search_restart(page) -> None:
    if page.current_candidate_id is None:
        return
    if page._search_session.owner_candidate_id != page.current_candidate_id:
        page._apply_search_prerequisite_state()
        return
    issue = page._search_prerequisite_issue()
    if issue:
        QMessageBox.warning(
            page,
            _t(page.ui_language, "岗位搜索结果", "Search Results"),
            issue,
        )
        page._apply_search_prerequisite_state()
        return
    if page._effective_search_settings() is None:
        return
    selected_duration_seconds = page._selected_search_duration_seconds()
    selected_duration_label = page._selected_search_duration_label()
    search_results_runtime_state.queue_search_restart_state(
        page,
        int(page.current_candidate_id),
        duration_seconds=selected_duration_seconds,
        duration_label=selected_duration_label,
        started_monotonic=time.monotonic(),
    )
    page._set_search_button_running(False, queued=True)
    page.search_duration_combo.setEnabled(False)
    page._refresh_search_countdown()
    page._search_countdown_timer.start()
    page._set_queued_restart_status(selected_duration_label)


def cancel_queued_search_restart(page) -> None:
    if not page._search_session.queued_for(page.current_candidate_id):
        page._apply_search_prerequisite_state()
        return
    page._clear_queued_search_restart()
    page._reset_search_countdown_state()
    page._set_search_button_running(False)
    page.search_duration_combo.setEnabled(True)
    page._set_stop_requested_status()


def run_search(page, candidate_id: int | None = None, *, run_busy_task_fn) -> None:
    target_candidate_id = candidate_id if candidate_id is not None else page.current_candidate_id
    if target_candidate_id is None:
        QMessageBox.information(
            page,
            _t(page.ui_language, "岗位搜索结果", "Search Results"),
            _t(page.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
        )
        return
    candidate = page.context.candidates.get(int(target_candidate_id))
    if candidate is None:
        QMessageBox.warning(
            page,
            _t(page.ui_language, "岗位搜索结果", "Search Results"),
            _t(page.ui_language, "当前求职者不存在，请重新选择。", "Candidate not found. Please select again."),
        )
        return

    profiles = page.context.profiles.list_for_candidate(int(target_candidate_id))
    issue = page._search_prerequisite_issue(
        candidate_id=int(target_candidate_id),
        profiles=profiles,
    )
    if issue:
        QMessageBox.warning(
            page,
            _t(page.ui_language, "岗位搜索结果", "Search Results"),
            issue,
        )
        page._apply_search_prerequisite_state(profiles=profiles)
        return
    active_profiles = [profile for profile in profiles if profile.is_active]

    pending_before_run = page._main_pending_analysis_count(int(target_candidate_id))
    selected_duration_seconds = page._selected_search_duration_seconds()
    selected_duration_label = page._selected_search_duration_label()
    effective_settings = page._effective_search_settings()
    if effective_settings is None:
        return
    queued_restart = (
        page._search_session.queued_for(int(target_candidate_id))
    )
    visible_owner = page.current_candidate_id == int(target_candidate_id)
    cancel_event = threading.Event()
    search_results_runtime_state.begin_search_session(
        page,
        int(target_candidate_id),
        cancel_event=cancel_event,
        duration_seconds=selected_duration_seconds,
        started_monotonic=time.monotonic(),
        preserve_started=queued_restart,
    )
    session_token = page._search_session.session_token
    if visible_owner:
        page._set_search_button_running(True)
        page.refresh_button.setEnabled(True)
        page.search_duration_combo.setEnabled(False)
        page._live_results_last_count = -1
        page._refresh_search_countdown()
        page._search_countdown_timer.start()
        page._set_running_status(
            candidate_name=candidate.name,
            pending_before_run=pending_before_run,
        )
        page._start_live_results_updates(int(target_candidate_id))

    def _task() -> Any:
        return page.runner.run_search(
            candidate=candidate,
            profiles=active_profiles,
            settings=effective_settings,
            api_base_url=page.context.settings.get_openai_base_url(),
            timeout_seconds=selected_duration_seconds,
            cancel_event=cancel_event,
        )

    def _is_current_session() -> bool:
        return (
            page._search_session.session_token == session_token
            and page._search_session.owner_candidate_id == int(target_candidate_id)
        )

    def _on_success(result: Any) -> None:
        if not _is_current_session():
            return
        if page.current_candidate_id != int(target_candidate_id):
            return
        if not hasattr(result, "success"):
            QMessageBox.warning(
                page,
                _t(page.ui_language, "岗位搜索结果", "Search Results"),
                _t(page.ui_language, "运行结果格式异常。", "Unexpected run result payload."),
            )
            return
        if getattr(result, "cancelled", False):
            if page._search_session.queued_for(int(target_candidate_id)):
                return
            jobs = _load_cumulative_table_jobs(page, int(target_candidate_id))
            page._render_jobs(jobs)
            page._refresh_results_stats_label()
            page._set_stopped_status(candidate.name)
            page._show_notification_toast(
                _t(
                    page.ui_language,
                    "岗位搜索已停止。",
                    "Job search stopped.",
                ),
                level="warning",
                duration_ms=4500,
            )
            return
        if not result.success:
            details = result.stderr_tail or result.stdout_tail or "No logs."
            page._set_failed_status(str(result.message or "").strip(), candidate.name)
            QMessageBox.warning(
                page,
                _t(page.ui_language, "岗位搜索结果", "Search Results"),
                f"{result.message}\n\nExit code: {result.exit_code}\n\n{details}",
            )
            return

        jobs = _load_cumulative_table_jobs(page, int(target_candidate_id))
        visible_count = page._render_jobs(jobs)
        page._refresh_results_stats_label()
        pending_after_run = page._main_pending_analysis_count(int(target_candidate_id))
        if _is_no_qualified_company_stop(getattr(result, "details", None)):
            detail_text = page._set_no_qualified_company_status(candidate.name)
            page._show_notification_toast(
                _t(
                    page.ui_language,
                    "本轮搜索已结束：当前没有新的合格公司。",
                    "This search round finished with no new qualified companies.",
                ),
                level="warning",
                duration_ms=5000,
            )
            QMessageBox.information(
                page,
                _t(page.ui_language, "岗位搜索结果", "Search Results"),
                search_results_status.search_completion_popup_message(
                    page.ui_language,
                    detail_text=detail_text,
                ),
            )
            return

        detail_text = page._set_finished_status(pending_after_run, candidate.name)
        page._show_notification_toast(
            (
                _t(
                    page.ui_language,
                    "本轮搜索已结束。",
                    "This search round finished.",
                )
                if pending_after_run <= 0
                else _t(
                    page.ui_language,
                    f"本轮搜索已结束，仍有 {pending_after_run} 条待补完岗位。",
                    f"This search round finished with {pending_after_run} pending job(s) remaining.",
                )
            ),
            level="success",
            duration_ms=5000,
        )
        QMessageBox.information(
            page,
            _t(page.ui_language, "岗位搜索结果", "Search Results"),
            search_results_status.search_completion_popup_message(
                page.ui_language,
                detail_text=detail_text,
            ),
        )

    def _on_error(exc: Exception) -> None:
        if not _is_current_session():
            return
        if page.current_candidate_id == int(target_candidate_id):
            page._set_failed_status(str(exc), candidate.name)
            page._refresh_results_stats_label()
        QMessageBox.warning(
            page,
            _t(page.ui_language, "岗位搜索结果", "Search Results"),
            str(exc),
        )

    def _on_finally() -> None:
        if not _is_current_session():
            return
        page._stop_live_results_updates()
        queued_restart = (
            page._search_session.queued_for(int(target_candidate_id))
        )
        if queued_restart:
            search_results_runtime_state.set_search_phase(page, "queued_restart")
            page._search_session.cancel_event = None
            if page.current_candidate_id == int(target_candidate_id):
                page._refresh_results_stats_label()
            QTimer.singleShot(0, lambda owner_id=int(target_candidate_id): page._run_search(candidate_id=owner_id))
            return
        page._reset_search_runtime_state()
        page._refresh_results_stats_label()
        page._apply_search_prerequisite_state()

    started = run_busy_task_fn(
        page,
        title=_t(page.ui_language, "岗位搜索结果", "Search Results"),
        message=_t(
            page.ui_language,
            f"系统正在后台搜索岗位（本次连续搜索时长：{selected_duration_label}）；只有发现新岗位或结果变化时才会刷新列表。你可以继续操作，搜索会在后台持续运行。",
            f"Searching jobs in the background for {selected_duration_label}; the list refreshes only when new jobs or updated results appear. You can keep working while search continues.",
        ),
        task=_task,
        on_success=_on_success,
        on_error=_on_error,
        on_finally=_on_finally,
        show_dialog=False,
    )
    if not started:
        page._reset_search_runtime_state()
        if visible_owner:
            page._stop_live_results_updates()
            page._set_results_progress_detail("")
            page._apply_search_prerequisite_state()


__all__ = [
    "cancel_queued_search_restart",
    "queue_search_restart",
    "run_search",
    "stop_search",
    "toggle_search",
]
