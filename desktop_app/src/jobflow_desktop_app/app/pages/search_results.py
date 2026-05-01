from __future__ import annotations

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ...db.repositories.candidates import CandidateRecord
from ...db.repositories.profiles import SearchProfileRecord
from ...db.repositories.settings import OpenAISettings
from ..context import AppContext
from ...search.orchestration import JobSearchResult, JobSearchRunner
from ..widgets.async_tasks import run_busy_task
from ..widgets.common import _t, styled_button
from . import search_results_links
from . import search_results_live_runtime
from . import search_results_live_state
from . import search_results_prerequisites
from . import search_results_rendering
from . import search_results_row_rendering
from . import search_results_candidate_state
from . import search_results_search_flow
from . import search_results_runtime_state
from . import search_results_review_state
from . import search_results_review_status
from . import search_results_status

class SearchResultsStep(QWidget):
    """Shared search-results behavior and widgets used by the current workspace."""

    STATUS_CODES = ("pending", "focus", "applied", "offered", "rejected", "dropped")
    BLOCKED_AI_LEVELS = {"missing", "invalid", "model_unverified", "warning", "error"}

    def __init__(self, context: AppContext, ui_language: str = "zh") -> None:
        super().__init__()
        self.context = context
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.runner = JobSearchRunner(context.paths.runtime_dir.parent, database=context.database)
        set_i18n_provider = getattr(self.runner, "set_job_display_i18n_context_provider", None)
        if callable(set_i18n_provider):
            set_i18n_provider(self._job_display_i18n_context)
        self._current_candidate: CandidateRecord | None = None
        self.target_role_candidates: list[str] = []
        self.status_by_job_key: dict[str, str] = {}
        self.hidden_job_keys: set[str] = set()
        self._search_session = search_results_runtime_state.SearchSessionState()
        self._live_results_timer = QTimer(self)
        self._live_results_timer.setInterval(2500)
        self._live_results_timer.timeout.connect(self._refresh_live_results)
        self._live_results_last_count = -1
        self._live_results_detail_text = ""
        self._live_results_progress_signature: tuple[str, str] = ("", "")
        self._live_results_signature: tuple[tuple[object, ...], ...] = ()
        self._ai_validation_level = "idle"
        self._ai_validation_message = ""
        self._target_ai_busy = False
        self._target_ai_busy_message = ""
        self._search_countdown_timer = QTimer(self)
        self._search_countdown_timer.setInterval(1000)
        self._search_countdown_timer.timeout.connect(self._refresh_search_countdown)
        self._notification_toast: QFrame | None = None
        self._notification_timer = QTimer(self)
        self._notification_timer.setSingleShot(True)
        self._notification_timer.timeout.connect(self._hide_notification_toast)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self._build_shared_widgets()
        self.refresh_button.clicked.connect(self._toggle_search)
        self.delete_button.clicked.connect(self._delete_selected_rows)
        self._set_search_button_running(False)
        self._set_search_countdown_seconds(0)

    def _build_shared_widgets(self) -> None:
        self.results_meta_label = QLabel(
            _t(
                self.ui_language,
                "请先选择一个求职者，并确认前面的目标岗位方向。",
                "Select a candidate and confirm target roles first.",
            ),
            self,
        )
        self.results_meta_label.setObjectName("MutedLabel")
        self.results_meta_label.setWordWrap(True)

        self.results_progress_label = QLabel("", self)
        self.results_progress_label.setObjectName("MutedLabel")
        self.results_progress_label.setWordWrap(True)
        self.results_progress_label.hide()

        self.results_stats_label = QLabel(
            _t(
                self.ui_language,
                "内部统计：未选择求职者。",
                "Internal stats: no candidate selected.",
            ),
            self,
        )
        self.results_stats_label.setObjectName("MutedLabel")
        self.results_stats_label.setWordWrap(True)

        self.search_duration_label = QLabel(
            _t(self.ui_language, "搜索时长", "Search Duration"),
            self,
        )
        self.search_duration_combo = QComboBox(self)
        self.search_duration_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.search_duration_combo.setMinimumContentsLength(8)
        self.search_duration_combo.setMinimumWidth(170)
        self.search_duration_combo.addItem(_t(self.ui_language, "1 小时", "1 hour"), 3600)
        self.search_duration_combo.addItem(_t(self.ui_language, "2 小时", "2 hours"), 7200)
        self.search_duration_combo.addItem(_t(self.ui_language, "3 小时", "3 hours"), 10800)
        self.search_duration_combo.addItem(_t(self.ui_language, "4 小时", "4 hours"), 14400)
        self.search_duration_combo.setCurrentIndex(0)
        self.search_duration_combo.currentIndexChanged.connect(self._refresh_search_countdown)

        self.search_countdown_label = QLabel(
            _t(self.ui_language, "剩余时间", "Remaining Time"),
            self,
        )
        self.search_countdown_value_label = QLabel("00:00:00", self)
        self.search_countdown_value_label.setObjectName("MutedLabel")

        self.refresh_button = styled_button(_t(self.ui_language, "开始搜索", "Start Search"), "primary")
        self.refresh_button.setParent(self)
        self.delete_button = styled_button(_t(self.ui_language, "删除所选岗位", "Delete Selected Jobs"), "danger")
        self.delete_button.setParent(self)

        self.table = QTableWidget(0, 8, self)
        self.table.setHorizontalHeaderLabels(
            [
                _t(self.ui_language, "职位名称", "Job Title"),
                _t(self.ui_language, "针对岗位", "Target Role"),
                _t(self.ui_language, "公司", "Company"),
                _t(self.ui_language, "地点", "Location"),
                _t(self.ui_language, "职位详情链接", "Job Details Link"),
                _t(self.ui_language, "发现时间", "Found Time"),
                _t(self.ui_language, "匹配性评分", "Match Score"),
                _t(self.ui_language, "状态", "Status"),
            ]
        )
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in range(self.table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 300)
        self.table.setColumnWidth(1, 240)
        self.table.setColumnWidth(2, 200)
        self.table.setColumnWidth(3, 160)
        self.table.setColumnWidth(4, 320)
        self.table.setColumnWidth(5, 150)
        self.table.setColumnWidth(6, 120)
        self.table.setColumnWidth(7, 130)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)

    @property
    def current_candidate_id(self) -> int | None:
        candidate = self._current_candidate
        if candidate is None or candidate.candidate_id is None:
            return None
        return int(candidate.candidate_id)

    @current_candidate_id.setter
    def current_candidate_id(self, candidate_id: int | None) -> None:
        self._current_candidate = (
            self.context.candidates.get(int(candidate_id))
            if candidate_id is not None
            else None
        )

    @property
    def current_candidate_name(self) -> str:
        candidate = self._current_candidate
        return str(candidate.name if candidate is not None else "").strip()

    @property
    def _busy_task_thread(self) -> QThread | None:
        return self._search_session.worker_thread

    @_busy_task_thread.setter
    def _busy_task_thread(self, value: QThread | None) -> None:
        self._search_session.worker_thread = value if isinstance(value, QThread) else None

    def _is_search_running(self, candidate_id: int | None = None) -> bool:
        if not self._search_session.is_active():
            return False
        if candidate_id is None:
            return True
        return self._search_session.owner_candidate_id == candidate_id

    def _search_owner_name(self, candidate_id: int | None) -> str:
        return search_results_prerequisites.search_owner_name(
            candidate_id,
            resolve_candidate_name=lambda target_candidate_id: (
                self.context.candidates.get(target_candidate_id).name
                if self.context.candidates.get(target_candidate_id) is not None
                else ""
            ),
        )

    def _active_profiles(self, candidate_id: int | None = None) -> list[SearchProfileRecord]:
        target_candidate_id = candidate_id if candidate_id is not None else self.current_candidate_id
        if target_candidate_id is None:
            return []
        return search_results_prerequisites.active_profiles(
            self.context.profiles.list_for_candidate(int(target_candidate_id))
        )

    def _search_prerequisite_issue(
        self,
        candidate_id: int | None = None,
        profiles: list[SearchProfileRecord] | None = None,
    ) -> str:
        target_candidate_id = candidate_id if candidate_id is not None else self.current_candidate_id
        ai_issue = self._blocked_ai_issue()
        candidate_profiles = (
            search_results_prerequisites.active_profiles(profiles)
            if profiles is not None
            else self._active_profiles(target_candidate_id)
        )
        return search_results_prerequisites.search_prerequisite_issue(
            self.ui_language,
            target_candidate_id=target_candidate_id,
            target_ai_busy=self._target_ai_busy,
            target_ai_busy_message=self._target_ai_busy_message,
            ai_issue=ai_issue,
            current_candidate_running=self._is_search_running(target_candidate_id),
            any_candidate_running=self._is_search_running(),
            owner_name=self._search_owner_name(self._search_session.owner_candidate_id),
            has_active_profiles=bool(candidate_profiles),
        )

    def _blocked_ai_issue(self) -> str:
        return search_results_prerequisites.blocked_ai_issue(
            self.ui_language,
            ai_validation_level=self._ai_validation_level,
            ai_validation_message=self._ai_validation_message,
            blocked_ai_levels=self.BLOCKED_AI_LEVELS,
        )

    def _clear_queued_search_restart(self) -> None:
        search_results_runtime_state.clear_queued_search_restart(self)

    def _reset_search_countdown_state(self) -> None:
        search_results_runtime_state.reset_search_countdown_state(self)

    def _reset_search_runtime_state(self) -> None:
        search_results_runtime_state.reset_search_runtime_state(self)

    def _apply_search_prerequisite_state(self, profiles: list[SearchProfileRecord] | None = None) -> None:
        target_candidate_id = self.current_candidate_id
        session = self._search_session
        if self._is_search_running(target_candidate_id) or session.queued_for(target_candidate_id) or (
            session.stop_requested and session.owner_candidate_id == target_candidate_id
        ):
            return
        has_candidate = self.current_candidate_id is not None
        issue = self._search_prerequisite_issue(profiles=profiles)
        self._set_search_button_running(False)
        self.refresh_button.setEnabled(has_candidate and not issue)
        self.search_duration_combo.setEnabled(has_candidate and not issue)
        self.refresh_button.setToolTip(issue)
        self.search_duration_combo.setToolTip(issue)
        if issue:
            self._set_results_progress_detail_with_level(issue, alert=True)
        else:
            self.results_progress_label.setStyleSheet("")

    def set_target_ai_busy_state(self, busy: bool, message: str = "") -> None:
        was_blocked = bool(self._target_ai_busy)
        self._target_ai_busy = bool(busy)
        self._target_ai_busy_message = str(message or "").strip() if self._target_ai_busy else ""
        self._apply_search_prerequisite_state()
        if (
            was_blocked
            and not self._target_ai_busy
            and not self._is_search_running(self.current_candidate_id)
            and self.current_candidate_id is not None
        ):
            search_results_candidate_state.reload_existing_results(self, int(self.current_candidate_id))

    def _set_results_main_status(self, text: str) -> None:
        message = str(text or "").strip()
        if self.results_meta_label.text() != message:
            self.results_meta_label.setText(message)

    def _set_results_progress_detail(self, text: str) -> None:
        self._set_results_progress_detail_with_level(text, alert=False)

    def _set_results_progress_detail_with_level(self, text: str, *, alert: bool) -> None:
        message = str(text or "").strip()
        if alert:
            self.results_progress_label.setStyleSheet("color: #b42318; font-weight: 600;")
        else:
            self.results_progress_label.setStyleSheet("")
        if self.results_progress_label.text() != message:
            self.results_progress_label.setText(message)
        self.results_progress_label.setVisible(bool(message))

    def _search_runtime_messages(
        self,
        state: str,
        *,
        candidate_name: str | None = None,
        pending_before_run: int | None = None,
        duration_label: str = "",
        stage_label: str = "",
        elapsed_text: str = "",
    ) -> tuple[str, str, str]:
        return search_results_status.search_runtime_messages(
            self.ui_language,
            state,
            candidate_name=str(candidate_name or self.current_candidate_name or "").strip(),
            pending_before_run=pending_before_run,
            duration_label=str(duration_label or "").strip()
            or self._selected_search_duration_label(),
            stage_label=stage_label,
            elapsed_text=elapsed_text,
        )

    def _candidate_status_text(
        self,
        zh_message: str,
        en_message: str,
        *,
        candidate_name: str | None = None,
    ) -> str:
        return search_results_status.candidate_status_text(
            self.ui_language,
            zh_message,
            en_message,
            candidate_name=str(
                candidate_name if candidate_name is not None else self.current_candidate_name or ""
            ).strip(),
        )

    def _set_no_candidate_status(self) -> None:
        search_results_candidate_state.set_no_candidate_status(self)

    def _set_ready_status(self, candidate_name: str | None = None) -> None:
        search_results_candidate_state.set_ready_status(self, candidate_name)

    def _set_loaded_results_status(self, visible_count: int, pending_count: int) -> None:
        search_results_candidate_state.set_loaded_results_status(
            self,
            visible_count,
            pending_count,
        )

    def _set_running_status(
        self,
        *,
        candidate_name: str | None = None,
        pending_before_run: int | None = None,
    ) -> None:
        main_message, detail_message, _dialog_message = self._search_runtime_messages(
            "running",
            candidate_name=candidate_name,
            pending_before_run=pending_before_run,
        )
        self._set_results_main_status(main_message)
        self._set_results_progress_detail(detail_message)

    def _set_stop_requested_status(self) -> None:
        main_message, detail_message, _dialog_message = self._search_runtime_messages(
            "stop_requested",
        )
        self._set_results_main_status(main_message)
        self._set_results_progress_detail(detail_message)

    def _set_queued_restart_status(self, duration_label: str) -> None:
        main_message, detail_message, _dialog_message = self._search_runtime_messages(
            "queued",
            duration_label=duration_label,
        )
        self._set_results_main_status(main_message)
        self._set_results_progress_detail(detail_message)

    def _set_stopped_status(self, candidate_name: str | None = None) -> None:
        self._set_results_main_status(
            self._candidate_status_text(
                "后台搜索已停止，当前保留已完成落盘的结果。",
                "Background search stopped. Any fully persisted results have been kept.",
                candidate_name=candidate_name,
            )
        )
        self._set_results_progress_detail(
            _t(
                self.ui_language,
                "如需继续补完岗位或继续搜索新的公司，可以再次点击“开始搜索”。",
                "Click Start Search again whenever you want to resume pending jobs or continue searching for new companies.",
            )
        )

    def _load_current_search_stats(self, candidate_id: int | None = None):
        target_candidate_id = candidate_id if candidate_id is not None else self.current_candidate_id
        if target_candidate_id is None:
            return None
        try:
            return self.runner.load_search_stats(int(target_candidate_id))
        except Exception:
            return None

    def _set_finished_status(self, pending_after_run: int, candidate_name: str | None = None) -> str:
        message = self._candidate_status_text(
            "本轮搜索已结束。",
            "This search round finished.",
            candidate_name=candidate_name,
        )
        self._set_results_main_status(message)
        stats = self._load_current_search_stats()
        detail_message = search_results_status.search_completion_detail(
            self.ui_language,
            discovered_job_count=getattr(stats, "main_discovered_job_count", 0),
            scored_job_count=getattr(stats, "main_scored_job_count", 0),
            recommended_job_count=getattr(stats, "recommended_job_count", 0),
            pending_job_count=pending_after_run,
            candidate_company_pool_count=getattr(stats, "candidate_company_pool_count", 0),
        )
        self._set_results_progress_detail(detail_message)
        return detail_message

    def _set_no_qualified_company_status(self, candidate_name: str | None = None) -> str:
        message = self._candidate_status_text(
            "本轮搜索已结束。",
            "This search round finished.",
            candidate_name=candidate_name,
        )
        self._set_results_main_status(message)
        stats = self._load_current_search_stats()
        detail_message = search_results_status.search_completion_detail(
            self.ui_language,
            discovered_job_count=getattr(stats, "main_discovered_job_count", 0),
            scored_job_count=getattr(stats, "main_scored_job_count", 0),
            recommended_job_count=getattr(stats, "recommended_job_count", 0),
            pending_job_count=getattr(stats, "main_pending_analysis_count", 0),
            candidate_company_pool_count=getattr(stats, "candidate_company_pool_count", 0),
            no_qualified_company_stop=True,
        )
        self._set_results_progress_detail(detail_message)
        return detail_message

    def _set_failed_status(self, reason: str, candidate_name: str | None = None) -> None:
        detail = str(reason or "").strip()
        if detail:
            message = self._candidate_status_text(
                f"搜索失败：{detail}",
                f"Search failed: {detail}",
                candidate_name=candidate_name,
            )
        else:
            message = self._candidate_status_text(
                "搜索失败，请查看弹窗中的错误日志。",
                "Search failed. Please check the error log in the dialog.",
                candidate_name=candidate_name,
            )
        self._set_results_main_status(message)
        self._set_results_progress_detail("")

    def _set_deleted_status(self, deleted_count: int) -> None:
        self._set_results_main_status(
            self._candidate_status_text(
                f"已删除 {deleted_count} 条岗位。",
                f"Deleted {deleted_count} row(s).",
            )
        )

    def set_candidate(self, candidate: CandidateRecord | None) -> None:
        same_candidate_running = (
            candidate is not None
            and candidate.candidate_id is not None
            and candidate.candidate_id == self.current_candidate_id
            and self._is_search_running(candidate.candidate_id)
        )
        if same_candidate_running:
            self._current_candidate = candidate
            profiles = self.context.profiles.list_for_candidate(candidate.candidate_id)
            self.target_role_candidates = self._build_target_role_candidates(profiles)
            self._load_review_state(candidate.candidate_id)
            session = self._search_session
            if session.stop_requested:
                if session.queued_restart:
                    self._set_search_button_running(False, queued=True)
                    self.search_duration_combo.setEnabled(False)
                    self._set_queued_restart_status(
                        session.queued_restart_duration_label or self._selected_search_duration_label()
                    )
                    self._refresh_search_countdown()
                    self._search_countdown_timer.start()
                else:
                    self._set_search_button_running(False)
                    self.search_duration_combo.setEnabled(True)
                    self._set_stop_requested_status()
            else:
                self._set_search_button_running(True)
                self.search_duration_combo.setEnabled(False)
                self._set_running_status(candidate_name=candidate.name)
            self._refresh_search_countdown()
            self._search_countdown_timer.start()
            self._start_live_results_updates(candidate.candidate_id)
            self.delete_button.setEnabled(True)
            self._refresh_results_stats_label()
            progress_detail_text, _progress_dialog_text = self._search_progress_text(candidate.candidate_id)
            self._set_results_progress_detail(progress_detail_text)
            self._live_results_detail_text = progress_detail_text
            return
        search_results_candidate_state.apply_non_running_candidate(self, candidate)

    def _reload_existing_results(self, candidate_id: int) -> None:
        search_results_candidate_state.reload_existing_results(self, candidate_id)
        self._apply_search_prerequisite_state()

    def set_ai_validation_state(self, message: str, level: str = "idle") -> None:
        was_blocked = bool(self._blocked_ai_issue())
        self._ai_validation_message = str(message or "").strip()
        self._ai_validation_level = str(level or "idle").strip().lower() or "idle"
        self._apply_search_prerequisite_state()
        if (
            was_blocked
            and not self._blocked_ai_issue()
            and not self._is_search_running(self.current_candidate_id)
            and self.current_candidate_id is not None
        ):
            search_results_candidate_state.reload_existing_results(self, int(self.current_candidate_id))

    def _effective_search_settings(self) -> OpenAISettings | None:
        settings = self.context.settings.get_effective_openai_settings()
        if settings.api_key.strip():
            detail = self._blocked_ai_issue()
            if detail:
                QMessageBox.warning(
                    self,
                    _t(self.ui_language, "岗位搜索结果", "Search Results"),
                    _t(
                        self.ui_language,
                        f"当前 AI 状态是红色，不能开始岗位搜索。请先在右上角“设置 / Settings”里修复后再试。\n\n当前状态：{detail}",
                        f"The current AI status is red, so job search cannot start. Please fix it in the top-right Settings / 设置 first.\n\nCurrent status: {detail}",
                    ),
                )
                return None
            return settings
        QMessageBox.warning(
            self,
            _t(self.ui_language, "岗位搜索结果", "Search Results"),
            _t(
                self.ui_language,
                "当前没有可用的 OpenAI API Key，不能开始岗位搜索。请先在右上角“设置 / Settings”里检查环境变量或填写并保存 Key。",
                "No usable OpenAI API key is available, so job search cannot start. Please check the environment variable or save a key in the top-right Settings / 设置 first.",
            ),
        )
        return None

    def _job_display_i18n_context(self) -> tuple[OpenAISettings | None, str]:
        return (
            self.context.settings.get_fast_openai_settings(),
            self.context.settings.get_openai_base_url(),
        )

    def _toggle_search(self) -> None:
        search_results_search_flow.toggle_search(self)

    def _stop_search(self) -> None:
        search_results_search_flow.stop_search(self)

    def _queue_search_restart(self) -> None:
        search_results_search_flow.queue_search_restart(self)

    def _cancel_queued_search_restart(self) -> None:
        search_results_search_flow.cancel_queued_search_restart(self)

    def shutdown_background_work(self, wait_ms: int = 8000) -> None:
        search_results_runtime_state.shutdown_background_work(self, wait_ms=wait_ms)

    def _run_search(self, candidate_id: int | None = None) -> None:
        search_results_search_flow.run_search(
            self,
            candidate_id=candidate_id,
            run_busy_task_fn=run_busy_task,
        )

    def _selected_search_duration_seconds(self) -> int:
        return search_results_runtime_state.selected_search_duration_seconds(self)

    def _selected_search_duration_label(self) -> str:
        return search_results_runtime_state.selected_search_duration_label(self)

    @staticmethod
    def _format_countdown_text(seconds: int) -> str:
        return search_results_status.format_countdown_text(seconds)

    def _set_search_countdown_seconds(self, seconds: int) -> None:
        search_results_runtime_state.set_search_countdown_seconds(self, seconds)

    def _refresh_search_countdown(self) -> None:
        search_results_runtime_state.refresh_search_countdown(self)

    def _set_search_button_running(self, running: bool, *, queued: bool = False) -> None:
        search_results_runtime_state.set_search_button_running(self, running, queued=queued)

    def _visible_jobs(self, jobs: list[JobSearchResult]) -> list[JobSearchResult]:
        return search_results_live_runtime.visible_jobs(self, jobs)

    def _job_render_signature(self, job: JobSearchResult) -> tuple[object, ...]:
        return search_results_live_runtime.job_render_signature(job)

    def _jobs_signature(self, jobs: list[JobSearchResult]) -> tuple[tuple[object, ...], ...]:
        return search_results_live_runtime.jobs_signature(jobs)

    def _sync_live_results_signature(self) -> None:
        search_results_live_runtime.sync_live_results_signature(self)

    def _main_pending_analysis_count(self, candidate_id: int | None = None) -> int:
        return search_results_live_runtime.main_pending_analysis_count(self, candidate_id)

    def _format_elapsed_text(self, seconds: int) -> str:
        return search_results_status.format_elapsed_text(self.ui_language, seconds)

    def _search_progress_text(self, candidate_id: int | None) -> tuple[str, str]:
        return search_results_live_runtime.search_progress_text(self, candidate_id)

    def _set_busy_task_message(self, text: str) -> None:
        search_results_runtime_state.set_busy_task_message(self, text)

    def _hide_notification_toast(self) -> None:
        search_results_runtime_state.hide_notification_toast(self)

    def _show_notification_toast(self, text: str, level: str = "info", duration_ms: int = 5000) -> None:
        search_results_runtime_state.show_notification_toast(
            self,
            text,
            level=level,
            duration_ms=duration_ms,
        )

    def _render_jobs(self, jobs: list[JobSearchResult]) -> int:
        return self._render_visible_jobs(self._visible_jobs(jobs))

    def _table_scroll_position(self) -> tuple[int, int]:
        return (
            self.table.verticalScrollBar().value(),
            self.table.horizontalScrollBar().value(),
        )

    def _restore_table_scroll_position(self, position: tuple[int, int]) -> None:
        vertical_value, horizontal_value = position
        vertical_bar = self.table.verticalScrollBar()
        horizontal_bar = self.table.horizontalScrollBar()
        vertical_bar.setValue(min(max(0, vertical_value), vertical_bar.maximum()))
        horizontal_bar.setValue(min(max(0, horizontal_value), horizontal_bar.maximum()))

    def _render_visible_jobs(self, visible_jobs: list[JobSearchResult]) -> int:
        scroll_position = self._table_scroll_position()
        self.table.setRowCount(0)
        for row_index, job in enumerate(visible_jobs):
            job_key = self._job_key(job)
            detail_url, _final_url, _link_status = self._job_link_details(job)
            search_results_row_rendering.populate_job_row(
                table=self.table,
                row_index=row_index,
                job=job,
                job_key=job_key,
                detail_url=detail_url,
                status_codes=self.STATUS_CODES,
                status_by_job_key=self.status_by_job_key,
                display_job_title=self._display_job_title,
                display_job_location=self._display_job_location,
                display_target_role=self._display_target_role,
                format_score=self._format_score,
                make_link_cell=self._make_link_cell,
                status_display=self._status_display,
                decorate_status_combo_items=self._decorate_status_combo_items,
                apply_status_combo_style=self._apply_status_combo_style,
                on_status_changed=self._on_status_changed,
            )
        self._live_results_signature = self._jobs_signature(visible_jobs)
        self._restore_table_scroll_position(scroll_position)
        return len(visible_jobs)

    def _make_link_cell(self, url: str, prefer_detail_label: bool) -> QLabel:
        return search_results_links.make_link_cell(
            self.ui_language,
            url,
            prefer_detail_label,
        )

    def _job_link_details(self, job: JobSearchResult) -> tuple[str, str, str]:
        return search_results_links.job_link_details(job)

    def _start_live_results_updates(self, candidate_id: int) -> None:
        search_results_live_runtime.start_live_results_updates(self, candidate_id)

    def _stop_live_results_updates(self) -> None:
        search_results_live_runtime.stop_live_results_updates(self)

    def _refresh_live_results(self) -> None:
        search_results_live_runtime.refresh_live_results(self)

    def _refresh_results_stats_label(self) -> None:
        search_results_candidate_state.refresh_results_stats_label(self)

    def _build_target_role_candidates(self, profiles: list[SearchProfileRecord]) -> list[str]:
        return search_results_candidate_state.build_target_role_candidates(profiles)

    def _display_target_role(self, job: JobSearchResult) -> str:
        return search_results_rendering.display_target_role(self.ui_language, job)

    def _display_job_title(self, job: JobSearchResult) -> str:
        return search_results_rendering.display_job_title(self.ui_language, job)

    def _display_job_location(self, job: JobSearchResult) -> str:
        return search_results_rendering.display_job_location(self.ui_language, job)

    def _format_score(self, job: JobSearchResult) -> str:
        return search_results_rendering.format_score(self.ui_language, job)

    @staticmethod
    def _job_key(job: JobSearchResult) -> str:
        return search_results_live_state.job_key(job)

    def _load_review_state(self, candidate_id: int) -> None:
        self.status_by_job_key, self.hidden_job_keys = search_results_review_state.load_review_state(
            self.context.settings,
            candidate_id,
            self._normalize_status_code,
        )

    def _save_review_state(self) -> None:
        search_results_review_state.save_review_state(
            self.context.settings,
            self.current_candidate_id,
            self.status_by_job_key,
            self.hidden_job_keys,
        )

    def _on_status_changed(self, job_key: str, status_text: str, combo: QComboBox | None = None) -> None:
        search_results_review_state.apply_status_change(
            status_by_job_key=self.status_by_job_key,
            job_key=job_key,
            status_text=status_text,
            normalize_status_code=self._normalize_status_code,
            apply_status_style=self._apply_status_combo_style,
            save_review_state=self._save_review_state,
            combo=combo,
        )

    def _delete_selected_rows(self) -> None:
        deleted_count = search_results_review_state.delete_selected_rows(
            owner=self,
            ui_language=self.ui_language,
            table=self.table,
            hidden_job_keys=self.hidden_job_keys,
            status_by_job_key=self.status_by_job_key,
            save_review_state=self._save_review_state,
            sync_live_results_signature=self._sync_live_results_signature,
            is_search_running=lambda: self._is_search_running(self.current_candidate_id),
            show_notification_toast=lambda text, level, duration_ms: self._show_notification_toast(
                text,
                level=level,
                duration_ms=duration_ms,
            ),
            set_deleted_status=self._set_deleted_status,
        )
        if deleted_count:
            self._live_results_last_count = self.table.rowCount()

    def _status_display(self, status_code: str) -> str:
        return search_results_status.status_display(self.ui_language, status_code)

    def _status_palette(self, status_code: str) -> dict[str, str]:
        return search_results_review_status.status_palette(status_code)

    def _decorate_status_combo_items(self, combo: QComboBox) -> None:
        search_results_review_status.decorate_status_combo_items(combo, self.STATUS_CODES)

    def _apply_status_combo_style(self, combo: QComboBox, status_code: str) -> None:
        search_results_review_status.apply_status_combo_style(
            combo,
            status_code,
        )

    def _normalize_status_code(self, value: str) -> str | None:
        return search_results_review_status.normalize_status_code(
            value,
            self.STATUS_CODES,
        )

