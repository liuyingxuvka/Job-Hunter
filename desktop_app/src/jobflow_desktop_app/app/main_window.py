from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
)

from ..db.repositories.settings import OpenAISettings
from ..services.app_context import AppContext
from ..services.model_catalog import fetch_available_models, filter_response_usable_models
from .theme import apply_theme
from .ui.pages import CandidateDirectoryPage, CandidateWorkspacePage


def _t(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh


class _AIValidationWorker(QObject):
    finished = Signal(object)

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context

    @Slot()
    def run(self) -> None:
        settings_repo = self.context.settings
        stored_settings = settings_repo.get_openai_settings()
        effective_settings = settings_repo.get_effective_openai_settings()
        api_key = effective_settings.api_key.strip()
        if not api_key:
            self.finished.emit(
                {
                    "state": "missing",
                    "stored_settings": stored_settings,
                }
            )
            return

        base_url = settings_repo.get_openai_base_url()
        result = fetch_available_models(
            api_key=api_key,
            api_base_url=base_url,
            timeout_seconds=8,
        )
        if not result.models:
            self.finished.emit(
                {
                    "state": "invalid",
                    "error": str(result.error or "").strip(),
                    "stored_settings": stored_settings,
                }
            )
            return

        preferred_models = [
            effective_settings.model.strip(),
            *settings_repo.get_openai_model_catalog(),
            "gpt-5-mini",
            "gpt-4.1-mini",
            "gpt-4o-mini",
            "gpt-4.1-nano",
            "gpt-5-nano",
            "gpt-3.5-turbo",
            "gpt-5",
        ]
        usable_models = filter_response_usable_models(
            api_key=api_key,
            models=result.models,
            api_base_url=base_url,
            timeout_seconds=6,
            max_probe=8,
            preferred_models=preferred_models,
            stop_after=4,
            probe_fallback=True,
        )
        current_model = effective_settings.model.strip()
        current_model_usable = bool(current_model) and any(
            item.casefold() == current_model.casefold() for item in usable_models
        )
        selected_model = current_model if current_model_usable else (usable_models[0] if usable_models else "")
        self.finished.emit(
            {
                "state": "ok" if usable_models else "model_unverified",
                "stored_settings": stored_settings,
                "current_model": current_model,
                "current_model_usable": current_model_usable,
                "selected_model": selected_model,
                "usable_models": usable_models,
            }
        )


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.ui_language = self.context.settings.get_ui_language()
        self.current_candidate_id: int | None = None
        self._ai_status_level = "idle"
        self._ai_status_model_name = ""
        self._ai_previous_model = ""
        self._ai_status_error = ""
        self._ai_validation_thread: QThread | None = None
        self._ai_validation_worker: _AIValidationWorker | None = None
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
            on_ai_settings_changed=self._start_ai_health_check,
        )
        self.stack.addWidget(self.candidate_directory_page)
        self.stack.addWidget(self.workspace_page)
        self._apply_ai_status()

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
        if self.stack.currentWidget() is self.workspace_page:
            self._start_ai_health_check()

    def _open_workspace(self) -> None:
        self.workspace_page.set_candidate(self.current_candidate_id)
        self.stack.setCurrentWidget(self.workspace_page)
        self._start_ai_health_check()

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
        self._apply_ai_status()
        if keep_workspace:
            self._start_ai_health_check()

    def _build_ai_status_texts(self) -> tuple[str, str]:
        level = self._ai_status_level
        model_name = str(self._ai_status_model_name or "").strip()
        previous_model = str(self._ai_previous_model or "").strip()
        error = str(self._ai_status_error or "").strip()
        if level == "checking":
            return (
                _t(self.ui_language, "正在验证", "Checking"),
                _t(
                    self.ui_language,
                    "正在后台验证 AI Key 和当前模型可用性。你可以继续操作，验证完成后会自动更新状态。",
                    "Validating the AI key and current model in the background. You can keep working while the status updates automatically.",
                ),
            )
        if level == "missing":
            return (
                _t(self.ui_language, "未配置", "Not configured"),
                _t(
                    self.ui_language,
                    "尚未检测到可用的 AI Key。请在右上角“AI 设置”中检查环境变量或直接填写 Key。",
                    "No usable AI key was detected yet. Check the environment variable or enter the key in 'AI Settings'.",
                ),
            )
        if level == "invalid":
            return (
                _t(self.ui_language, "校验失败", "Validation failed"),
                _t(
                    self.ui_language,
                    f"AI Key 校验失败。请打开右上角“AI 设置”检查 Key、环境变量或接口地址。{('错误：' + error) if error else ''}",
                    f"AI key validation failed. Open 'AI Settings' in the top-right to check the key, environment variable, or API base URL.{(' Error: ' + error) if error else ''}",
                ),
            )
        if level == "model_unverified":
            return (
                _t(self.ui_language, "模型待确认", "Model needs re-check"),
                _t(
                    self.ui_language,
                    "AI Key 已读取到，但当前保存模型未通过快速验证。建议打开“AI 设置”重新检测模型。",
                    "The AI key was found, but the saved model did not pass quick validation. Open 'AI Settings' and detect models again.",
                ),
            )
        if level == "switched":
            return (
                _t(
                    self.ui_language,
                    f"已切换到 {model_name}" if model_name else "已自动切换模型",
                    f"Switched to {model_name}" if model_name else "Model switched",
                ),
                _t(
                    self.ui_language,
                    f"AI 已验证可用，但原模型 {previous_model or '未设置'} 当前不可用，已自动切换为 {model_name}。",
                    f"AI is verified and ready, but the previous model {previous_model or 'not set'} is not usable now, so it was switched to {model_name}.",
                ),
            )
        if level == "ready":
            return (
                _t(
                    self.ui_language,
                    f"可用：{model_name}" if model_name else "可用",
                    f"Ready: {model_name}" if model_name else "Ready",
                ),
                _t(
                    self.ui_language,
                    f"AI 已验证可用，当前模型 {model_name} 可以直接使用。",
                    f"AI is verified and ready. Current model {model_name} can be used directly.",
                ),
            )
        return (
            _t(self.ui_language, "未检查", "Not checked"),
            _t(
                self.ui_language,
                "进入求职者工作台后会自动验证 AI Key 和当前模型。",
                "The AI key and current model will be checked automatically after entering the workspace.",
            ),
        )

    def _apply_ai_status(self) -> None:
        _summary, detail = self._build_ai_status_texts()
        self.workspace_page.set_ai_validation_status(detail, self._ai_status_level)
        self._update_status_bar()

    def _set_ai_status(
        self,
        level: str,
        *,
        model_name: str = "",
        previous_model: str = "",
        error: str = "",
    ) -> None:
        self._ai_status_level = str(level or "idle").strip() or "idle"
        self._ai_status_model_name = str(model_name or "").strip()
        self._ai_previous_model = str(previous_model or "").strip()
        self._ai_status_error = str(error or "").strip()
        self._apply_ai_status()

    def _update_status_bar(self) -> None:
        prefix = f"DB: {self.context.paths.db_path}"
        no_candidate = _t(self.ui_language, "未选择", "Not selected")
        label = _t(self.ui_language, "当前求职者", "Current Candidate")
        ai_label = _t(self.ui_language, "AI", "AI")
        ai_summary, _detail = self._build_ai_status_texts()
        suffix = f"  |  {ai_label}: {ai_summary}"
        if self.current_candidate_id is None:
            self.statusBar().showMessage(f"{prefix}  |  {label}: {no_candidate}{suffix}")
            return

        record = self.context.candidates.get(self.current_candidate_id)
        if record is None:
            self.statusBar().showMessage(f"{prefix}  |  {label}: {no_candidate}{suffix}")
            return
        self.statusBar().showMessage(
            f"{prefix}  |  {label}: {record.name}{suffix}"
        )

    def _warmup_model_catalog(self) -> None:
        self._start_ai_health_check()

    def _start_ai_health_check(self) -> None:
        if self._ai_validation_thread is not None and self._ai_validation_thread.isRunning():
            self._set_ai_status("checking")
            return

        self._set_ai_status("checking")
        worker = _AIValidationWorker(self.context)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_ai_health_check_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._ai_validation_worker = worker
        self._ai_validation_thread = thread
        thread.start()

    @Slot(object)
    def _on_ai_health_check_finished(self, payload: object) -> None:
        self._ai_validation_worker = None
        self._ai_validation_thread = None
        data = payload if isinstance(payload, dict) else {}
        state = str(data.get("state") or "").strip()
        if state == "missing":
            self._set_ai_status("missing")
            return
        if state == "invalid":
            self._set_ai_status("invalid", error=str(data.get("error") or ""))
            return
        if state == "model_unverified":
            self._set_ai_status("model_unverified")
            return
        if state != "ok":
            self._set_ai_status("invalid")
            return

        stored_settings = data.get("stored_settings")
        current_model = str(data.get("current_model") or "").strip()
        current_model_usable = bool(data.get("current_model_usable"))
        selected_model = str(data.get("selected_model") or "").strip()
        usable_models = data.get("usable_models") if isinstance(data.get("usable_models"), list) else []

        if usable_models:
            merged_catalog = self.context.settings.get_openai_model_catalog()
            seen = {item.casefold() for item in merged_catalog}
            for item in usable_models:
                text = str(item or "").strip()
                if not text or text.casefold() in seen:
                    continue
                seen.add(text.casefold())
                merged_catalog.append(text)
            self.context.settings.save_openai_model_catalog(merged_catalog)

        if (
            selected_model
            and not current_model_usable
            and isinstance(stored_settings, OpenAISettings)
        ):
            self.context.settings.save_openai_settings(
                OpenAISettings(
                    api_key=stored_settings.api_key,
                    model=selected_model,
                    api_key_source=stored_settings.api_key_source,
                    api_key_env_var=stored_settings.api_key_env_var,
                )
            )
            self._set_ai_status(
                "switched",
                model_name=selected_model,
                previous_model=current_model,
            )
            return

        if current_model_usable:
            self._set_ai_status("ready", model_name=selected_model or current_model)
            return

        if selected_model:
            self._set_ai_status(
                "switched",
                model_name=selected_model,
                previous_model=current_model,
            )
            return

        self._set_ai_status("model_unverified")


def run_desktop_app(context: AppContext) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow(context)
    window.show()
    return app.exec()
