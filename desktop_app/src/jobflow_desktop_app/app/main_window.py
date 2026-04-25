from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
)

from .context import AppContext
from ..ai.client import (
    OpenAIResponsesClient,
    OpenAIResponsesError,
    build_text_input_messages,
    parse_response_json,
)
from ..ai.model_catalog import fetch_available_models
from .theme import apply_theme
from .pages.candidate_directory import CandidateDirectoryPage
from .pages.workspace_compact import CandidateWorkspaceCompactPage


def _t(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh


def _resolve_app_icon(context: AppContext) -> QIcon | None:
    candidates = (
        context.paths.project_root / "assets" / "app_icon.png",
        context.paths.project_root / "assets" / "app_icon.ico",
    )
    for path in candidates:
        if not isinstance(path, Path) or not path.exists():
            continue
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    return None


def _validate_structured_model_request(
    api_key: str,
    model_id: str,
    *,
    api_base_url: str = "",
    timeout_seconds: int = 8,
) -> tuple[bool, str]:
    client = OpenAIResponsesClient(
        api_key=str(api_key or "").strip(),
        api_base_url=str(api_base_url or "").strip(),
        timeout_seconds=max(1, int(timeout_seconds)),
    )
    try:
        response = client.create_json_schema(
            model=str(model_id or "").strip(),
            input_payload=build_text_input_messages(
                "Return only JSON that matches the schema.",
                "Respond with ok=true.",
            ),
            schema_name="jobflow_model_validation",
            schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                },
                "required": ["ok"],
                "additionalProperties": False,
            },
        )
        parsed = parse_response_json(response, "AI model structured validation")
    except OpenAIResponsesError as exc:
        return False, str(exc or "").strip()
    if parsed.get("ok") is True:
        return True, ""
    return False, "AI model validation returned an unexpected JSON payload."


def _format_model_pair(*, fast_model: str, quality_model: str) -> str:
    fast = str(fast_model or "").strip() or "not set"
    quality = str(quality_model or "").strip() or "not set"
    return f"fast {fast}; quality {quality}"


class _AIValidationWorker(QObject):
    finished = Signal(object)

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context

    @Slot()
    def run(self) -> None:
        settings_repo = self.context.settings
        effective_settings = settings_repo.get_effective_openai_settings()
        api_key = effective_settings.api_key.strip()
        if not api_key:
            self.finished.emit(
                {
                    "state": "missing",
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
                }
            )
            return

        fast_model = effective_settings.fast_model
        quality_model = effective_settings.quality_model.strip()
        model_label = _format_model_pair(fast_model=fast_model, quality_model=quality_model)
        missing_parts: list[str] = []
        if not fast_model:
            missing_parts.append("fast model")
        if not quality_model:
            missing_parts.append("quality model")
        if missing_parts:
            self.finished.emit(
                {
                    "state": "model_unverified",
                    "current_model": model_label,
                    "fast_model": fast_model,
                    "quality_model": quality_model,
                    "error": "No " + " or ".join(missing_parts) + " is currently saved.",
                }
            )
            return

        catalog_keys = {str(item or "").strip().casefold() for item in result.models}
        missing_catalog_models: list[str] = []
        if fast_model.casefold() not in catalog_keys:
            missing_catalog_models.append(f"fast model {fast_model}")
        if quality_model.casefold() not in catalog_keys:
            missing_catalog_models.append(f"quality model {quality_model}")
        if missing_catalog_models:
            self.finished.emit(
                {
                    "state": "model_unverified",
                    "current_model": model_label,
                    "fast_model": fast_model,
                    "quality_model": quality_model,
                    "error": "The saved "
                    + " and ".join(missing_catalog_models)
                    + " is not present in the current model catalog.",
                }
            )
            return

        fast_model_usable, fast_validation_error = _validate_structured_model_request(
            api_key=api_key,
            model_id=fast_model,
            api_base_url=base_url,
            timeout_seconds=8,
        )
        if not fast_model_usable:
            self.finished.emit(
                {
                    "state": "model_unverified",
                    "current_model": model_label,
                    "fast_model": fast_model,
                    "quality_model": quality_model,
                    "fast_model_usable": False,
                    "quality_model_usable": False,
                    "error": f"Fast model {fast_model} failed validation. {str(fast_validation_error or '').strip()}",
                }
            )
            return

        quality_model_usable, quality_validation_error = _validate_structured_model_request(
            api_key=api_key,
            model_id=quality_model,
            api_base_url=base_url,
            timeout_seconds=8,
        )
        self.finished.emit(
            {
                "state": "ok" if quality_model_usable else "model_unverified",
                "current_model": model_label,
                "fast_model": fast_model,
                "quality_model": quality_model,
                "current_model_usable": bool(fast_model_usable and quality_model_usable),
                "fast_model_usable": fast_model_usable,
                "quality_model_usable": quality_model_usable,
                "error": (
                    ""
                    if quality_model_usable
                    else f"Quality model {quality_model} failed validation. {str(quality_validation_error or '').strip()}"
                ),
            }
        )


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.ui_language = self.context.settings.get_ui_language()
        self._ai_status_level = "idle"
        self._ai_status_model_name = ""
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

    @property
    def current_candidate_id(self) -> int | None:
        page = getattr(self, "candidate_directory_page", None)
        if not isinstance(page, CandidateDirectoryPage):
            return None
        return page.selected_candidate_id()

    @current_candidate_id.setter
    def current_candidate_id(self, candidate_id: int | None) -> None:
        page = getattr(self, "candidate_directory_page", None)
        if not isinstance(page, CandidateDirectoryPage):
            return
        page.set_selected_candidate_id(candidate_id, emit_selection=False)

    def _shutdown_ai_validation(self, wait_ms: int = 2000) -> None:
        thread = self._ai_validation_thread
        if isinstance(thread, QThread) and thread.isRunning():
            thread.wait(max(0, int(wait_ms)))

    def closeEvent(self, event: QCloseEvent) -> None:
        try:
            if isinstance(getattr(self, "workspace_compact_page", None), CandidateWorkspaceCompactPage):
                self.workspace_compact_page.shutdown_background_work(wait_ms=8000)
            # Validation can spend up to ~24 seconds across model list fetch and
            # two structured JSON smoke requests, so give shutdown enough headroom
            # to avoid tearing
            # down Qt while the worker thread is still alive.
            self._shutdown_ai_validation(wait_ms=30000)
        finally:
            super().closeEvent(event)

    def _build_pages(self) -> None:
        self.candidate_directory_page = CandidateDirectoryPage(
            self.context,
            ui_language=self.ui_language,
            on_data_changed=self.refresh,
            on_candidate_selected=self._set_current_candidate,
            on_open_workspace=self._open_workspace,
            on_ui_language_changed=self._on_ui_language_changed,
        )
        self.workspace_compact_page = CandidateWorkspaceCompactPage(
            self.context,
            ui_language=self.ui_language,
            on_data_changed=self.refresh,
            on_back_to_candidates=self._show_candidates_page,
            on_ui_language_changed=self._on_ui_language_changed,
            on_ai_settings_changed=self._start_ai_health_check,
        )
        self.stack.addWidget(self.candidate_directory_page)
        self.stack.addWidget(self.workspace_compact_page)
        self._apply_ai_status()

    def refresh(self) -> None:
        candidate_id = self.current_candidate_id
        if candidate_id is not None and self.context.candidates.get(candidate_id) is None:
            candidate_id = None
        self.candidate_directory_page.reload(select_candidate_id=candidate_id, emit_selection=False)
        if candidate_id is None and self.candidate_directory_page.records:
            candidate_id = self.candidate_directory_page.records[0].candidate_id
            self.candidate_directory_page.set_selected_candidate_id(
                candidate_id,
                emit_selection=False,
            )
        self.current_candidate_id = candidate_id
        self.workspace_compact_page.set_candidate(candidate_id)
        self._update_status_bar()

    def _set_current_candidate(self, candidate_id: int | None) -> None:
        self.current_candidate_id = candidate_id
        self.workspace_compact_page.set_candidate(candidate_id)
        self._update_status_bar()
        if self.stack.currentWidget() is self.workspace_compact_page:
            self._start_ai_health_check()

    def _open_workspace(self, candidate_id: int | None = None) -> None:
        candidate_id = candidate_id if candidate_id is not None else self.current_candidate_id
        if candidate_id is None:
            self.stack.setCurrentWidget(self.candidate_directory_page)
            return
        if candidate_id != self.current_candidate_id:
            self._set_current_candidate(candidate_id)
        else:
            self.workspace_compact_page.set_candidate(candidate_id)
        self.workspace_compact_page.show_initial_step()
        self.stack.setCurrentWidget(self.workspace_compact_page)
        self._start_ai_health_check()

    def _show_candidates_page(self) -> None:
        self.stack.setCurrentWidget(self.candidate_directory_page)

    def _on_ui_language_changed(self, language: str) -> None:
        normalized = "en" if language == "en" else "zh"
        if normalized == self.ui_language:
            return
        keep_compact_workspace = self.stack.currentWidget() is self.workspace_compact_page
        QTimer.singleShot(
            0,
            lambda lang=normalized, compact=keep_compact_workspace: self._apply_ui_language(lang, compact),
        )

    def _apply_ui_language(self, language: str, keep_compact_workspace: bool) -> None:
        normalized = "en" if language == "en" else "zh"
        self.ui_language = normalized
        self.context.settings.save_ui_language(normalized)
        self.setWindowTitle(_t(self.ui_language, "求职工作台", "Jobflow Desktop App"))

        old_candidate_page = self.candidate_directory_page
        old_workspace_compact_page = self.workspace_compact_page
        old_workspace_compact_page.shutdown_background_work(wait_ms=8000)
        self.stack.removeWidget(old_candidate_page)
        self.stack.removeWidget(old_workspace_compact_page)
        old_candidate_page.deleteLater()
        old_workspace_compact_page.deleteLater()

        self._build_pages()
        if keep_compact_workspace:
            self.stack.setCurrentWidget(self.workspace_compact_page)
        else:
            self.stack.setCurrentWidget(self.candidate_directory_page)
        self.refresh()
        self._apply_ai_status()
        if keep_compact_workspace:
            self._start_ai_health_check()

    def _build_ai_status_texts(self) -> tuple[str, str]:
        level = self._ai_status_level
        model_name = str(self._ai_status_model_name or "").strip()
        error = str(self._ai_status_error or "").strip()
        if level == "checking":
            return (
                _t(self.ui_language, "正在验证", "Checking"),
                _t(
                    self.ui_language,
                    "正在后台验证 AI Key、快速模型和高质量模型的可用性。你可以继续操作，验证完成后会自动更新状态。",
                    "Validating the AI key, fast model, and quality model in the background. You can keep working while the status updates automatically.",
                ),
            )
        if level == "missing":
            return (
                _t(self.ui_language, "未配置", "Not configured"),
                _t(
                    self.ui_language,
                    "未检测到可用 AI Key。请在“设置 / Settings”中配置。",
                    "No usable AI key was detected. Configure it in Settings.",
                ),
            )
        if level == "invalid":
            return (
                _t(self.ui_language, "校验失败", "Validation failed"),
                _t(
                    self.ui_language,
                    "AI Key 校验失败。请在“设置 / Settings”检查 Key 或接口地址。",
                    "AI key validation failed. Check the key or API base URL in Settings.",
                ),
            )
        if level == "model_unverified":
            return (
                _t(self.ui_language, "模型待确认", "Model needs re-check"),
                _t(
                    self.ui_language,
                    (
                        f"AI Key 已读取，但模型未验证（{model_name or '未设置'}）。"
                        "请在“设置 / Settings”重新加载并选择模型。"
                    ),
                    (
                        f"AI key found, but models are not verified ({model_name or 'not set'}). "
                        "Reload and choose models in Settings."
                    ),
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
                    f"AI 已验证可用，当前模型设置为 {model_name}。",
                    f"AI is verified and ready. Current model settings are {model_name}.",
                ),
            )
        return (
            _t(self.ui_language, "未检查", "Not checked"),
            _t(
                self.ui_language,
                "进入求职者工作台后会自动验证 AI Key、快速模型和高质量模型。",
                "The AI key, fast model, and quality model will be checked automatically after entering the workspace.",
            ),
        )

    def _apply_ai_status(self) -> None:
        _summary, detail = self._build_ai_status_texts()
        self.workspace_compact_page.set_ai_validation_status(detail, self._ai_status_level)
        self._update_status_bar()

    def _set_ai_status(
        self,
        level: str,
        *,
        model_name: str = "",
        error: str = "",
    ) -> None:
        self._ai_status_level = str(level or "idle").strip() or "idle"
        self._ai_status_model_name = str(model_name or "").strip()
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

    def _format_ai_status_model_name(self, data: dict) -> str:
        fast_model = str(data.get("fast_model") or "").strip()
        quality_model = str(data.get("quality_model") or "").strip()
        if fast_model or quality_model:
            return _t(
                self.ui_language,
                f"快速 {fast_model or '未设置'}；高质量 {quality_model or '未设置'}",
                f"fast {fast_model or 'not set'}; quality {quality_model or 'not set'}",
            )
        return str(data.get("current_model") or "").strip()

    def _warmup_model_catalog(self) -> None:
        self._start_ai_health_check()

    def _start_ai_health_check(self) -> None:
        if self._ai_validation_thread is not None and self._ai_validation_thread.isRunning():
            self._set_ai_status("checking")
            return

        self._set_ai_status("checking")
        worker = _AIValidationWorker(self.context)
        thread_parent = QApplication.instance()
        thread = QThread(thread_parent if isinstance(thread_parent, QObject) else None)
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
            self._set_ai_status(
                "model_unverified",
                model_name=self._format_ai_status_model_name(data),
                error=str(data.get("error") or "").strip(),
            )
            return
        if state != "ok":
            self._set_ai_status("invalid")
            return

        current_model = self._format_ai_status_model_name(data)
        current_model_usable = bool(data.get("current_model_usable"))
        if current_model_usable:
            self._set_ai_status("ready", model_name=current_model)
            return

        self._set_ai_status(
            "model_unverified",
            model_name=current_model,
            error=str(data.get("error") or "").strip(),
        )


def run_desktop_app(context: AppContext) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app_icon = _resolve_app_icon(context)
    if app_icon is not None:
        app.setWindowIcon(app_icon)
    apply_theme(app)
    window = MainWindow(context)
    if app_icon is not None:
        window.setWindowIcon(app_icon)
    window.show()
    return app.exec()
