from __future__ import annotations

import sys
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
)

from ..services.app_context import AppContext
from ..services.model_catalog import fetch_available_models, filter_response_usable_models
from .theme import apply_theme
from .ui.pages import CandidateDirectoryPage, CandidateWorkspacePage


def _t(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.ui_language = self.context.settings.get_ui_language()
        self.current_candidate_id: int | None = None
        self.setWindowTitle(_t(self.ui_language, "求职工作台", "Jobflow Desktop App"))
        self.resize(1280, 860)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self._build_pages()

        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        self.stack.setCurrentWidget(self.candidate_directory_page)
        self.refresh()
        QTimer.singleShot(0, self._warmup_model_catalog)

    def _build_pages(self) -> None:
        self.candidate_directory_page = CandidateDirectoryPage(
            self.context,
            ui_language=self.ui_language,
            on_data_changed=self.refresh,
            on_candidate_selected=self._set_current_candidate,
            on_open_workspace=self._open_workspace,
            on_ui_language_changed=self._on_ui_language_changed,
        )
        self.workspace_page = CandidateWorkspacePage(
            self.context,
            ui_language=self.ui_language,
            on_data_changed=self.refresh,
            on_back_to_candidates=self._show_candidates_page,
            on_ui_language_changed=self._on_ui_language_changed,
        )
        self.stack.addWidget(self.candidate_directory_page)
        self.stack.addWidget(self.workspace_page)

    def refresh(self) -> None:
        self.candidate_directory_page.reload(select_candidate_id=self.current_candidate_id)

        if self.current_candidate_id is not None and self.context.candidates.get(self.current_candidate_id) is None:
            self.current_candidate_id = None

        self.workspace_page.set_candidate(self.current_candidate_id)
        self._update_status_bar()

    def _set_current_candidate(self, candidate_id: int | None) -> None:
        self.current_candidate_id = candidate_id
        self.workspace_page.set_candidate(candidate_id)
        self._update_status_bar()

    def _open_workspace(self) -> None:
        self.workspace_page.set_candidate(self.current_candidate_id)
        self.stack.setCurrentWidget(self.workspace_page)

    def _show_candidates_page(self) -> None:
        self.stack.setCurrentWidget(self.candidate_directory_page)

    def _on_ui_language_changed(self, language: str) -> None:
        normalized = "en" if language == "en" else "zh"
        if normalized == self.ui_language:
            return
        keep_workspace = self.stack.currentWidget() is self.workspace_page
        QTimer.singleShot(
            0,
            lambda lang=normalized, workspace=keep_workspace: self._apply_ui_language(lang, workspace),
        )

    def _apply_ui_language(self, language: str, keep_workspace: bool) -> None:
        normalized = "en" if language == "en" else "zh"
        self.ui_language = normalized
        self.context.settings.save_ui_language(normalized)
        self.setWindowTitle(_t(self.ui_language, "求职工作台", "Jobflow Desktop App"))

        old_candidate_page = self.candidate_directory_page
        old_workspace_page = self.workspace_page
        self.stack.removeWidget(old_candidate_page)
        self.stack.removeWidget(old_workspace_page)
        old_candidate_page.deleteLater()
        old_workspace_page.deleteLater()

        self._build_pages()
        self.stack.setCurrentWidget(self.workspace_page if keep_workspace else self.candidate_directory_page)
        self.refresh()

    def _update_status_bar(self) -> None:
        prefix = f"DB: {self.context.paths.db_path}"
        no_candidate = _t(self.ui_language, "未选择", "Not selected")
        label = _t(self.ui_language, "当前求职者", "Current Candidate")
        if self.current_candidate_id is None:
            self.statusBar().showMessage(f"{prefix}  |  {label}: {no_candidate}")
            return

        record = self.context.candidates.get(self.current_candidate_id)
        if record is None:
            self.statusBar().showMessage(f"{prefix}  |  {label}: {no_candidate}")
            return
        self.statusBar().showMessage(
            f"{prefix}  |  {label}: {record.name}"
        )

    def _warmup_model_catalog(self) -> None:
        settings = self.context.settings.get_effective_openai_settings()
        api_key = settings.api_key.strip()
        if not api_key:
            return

        base_url = self.context.settings.get_openai_base_url()

        def worker() -> None:
            result = fetch_available_models(api_key=api_key, api_base_url=base_url, timeout_seconds=5)
            if not result.models:
                return
            models = filter_response_usable_models(
                api_key=api_key,
                models=result.models,
                api_base_url=base_url,
                timeout_seconds=4,
                max_probe=4,
                preferred_models=[
                    settings.model.strip(),
                ],
                stop_after=1,
                probe_fallback=False,
            )
            if models:
                self.context.settings.save_openai_model_catalog(models)

        threading.Thread(target=worker, daemon=True).start()


def run_desktop_app(context: AppContext) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow(context)
    window.show()
    return app.exec()
