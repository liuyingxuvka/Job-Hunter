from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QThread, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...db.repositories.candidates import CandidateRecord, CandidateSummary
from ...db.repositories.profiles import SearchProfileRecord
from ...db.repositories.settings import OpenAISettings
from ...services.app_context import AppContext
from ...services.legacy_runner import LegacyJobResult, LegacyJobflowRunner
from ...services.location_structured import (
    decode_base_location_struct,
    decode_preferred_locations_struct,
    dedup_location_entries,
    encode_base_location_struct,
    encode_preferred_locations_struct,
    location_entry_display,
    location_type_suggestions,
    normalize_location_entry,
    preferred_locations_plain_text,
)
from ...services.model_catalog import fetch_available_models
from ...services.role_recommendations import (
    OpenAIRoleRecommendationService,
    RoleRecommendationError,
    decode_bilingual_role_name,
    decode_bilingual_description,
    description_for_prompt,
    encode_bilingual_role_name,
    encode_bilingual_description,
    is_generic_role_name,
    role_name_query_lines,
    select_bilingual_role_name,
    select_bilingual_description,
)


SUPPORT_PAYPAL_EMAIL_ENV = "JOBFLOW_SUPPORT_PAYPAL_EMAIL"
SUPPORT_PAYPAL_EMAIL_SETTING_KEY = "support_paypal_email"
SUPPORT_PAYPAL_EMAIL_DEFAULT = "liu.yingxu.vka@gmail.com"


def styled_button(text: str, variant: str = "secondary") -> QPushButton:
    button = QPushButton(text)
    button.setProperty("variant", variant)
    return button


def make_card() -> QFrame:
    frame = QFrame()
    frame.setProperty("card", True)
    frame.setFrameShape(QFrame.StyledPanel)
    return frame


def make_scroll_area(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    return scroll


def make_page_title(title: str, subtitle: str) -> QWidget:
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)

    title_label = QLabel(title)
    title_label.setObjectName("PageTitle")
    subtitle_label = QLabel(subtitle)
    subtitle_label.setObjectName("PageSubtitle")
    subtitle_label.setWordWrap(True)

    layout.addWidget(title_label)
    layout.addWidget(subtitle_label)
    return wrapper


def _t(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh


class _BackgroundTaskWorker(QObject):
    finished = Signal(object, object)

    def __init__(self, task: Callable[[], Any]) -> None:
        super().__init__()
        self._task = task

    def run(self) -> None:
        try:
            result = self._task()
            self.finished.emit(result, None)
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.finished.emit(None, exc)


class _BusyTaskRelay(QObject):
    def __init__(self, callback: Callable[[Any, object], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._callback = callback

    @Slot(object, object)
    def on_finished(self, result: Any, error: object) -> None:
        self._callback(result, error)


def run_busy_task(
    owner: QWidget,
    *,
    title: str,
    message: str,
    task: Callable[[], Any],
    on_success: Callable[[Any], None],
    on_error: Callable[[Exception], None] | None = None,
    on_finally: Callable[[], None] | None = None,
    timeout_ms: int | None = None,
    show_dialog: bool = True,
) -> bool:
    running_thread = getattr(owner, "_busy_task_thread", None)
    if isinstance(running_thread, QThread) and running_thread.isRunning():
        return False

    dialog: QProgressDialog | None = None
    if show_dialog:
        dialog = QProgressDialog(message, "", 0, 0, owner)
        dialog.setWindowTitle(title)
        dialog.setWindowModality(Qt.NonModal)
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dialog.setCancelButton(None)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.setRange(0, 0)
        dialog.setValue(0)
        dialog.show()
        QApplication.processEvents()

    worker = _BackgroundTaskWorker(task)
    thread = QThread(owner)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    completed = False
    timeout_timer: QTimer | None = None

    def _finish(result: Any, error: object) -> None:
        nonlocal completed
        if completed:
            return
        completed = True
        if timeout_timer is not None:
            timeout_timer.stop()
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        setattr(owner, "_busy_task_thread", None)
        setattr(owner, "_busy_task_worker", None)
        setattr(owner, "_busy_task_dialog", None)
        setattr(owner, "_busy_task_relay", None)
        if on_finally is not None:
            on_finally()
        if error is not None:
            if on_error is not None:
                if isinstance(error, Exception):
                    on_error(error)
                else:
                    on_error(RuntimeError(str(error)))
            return
        on_success(result)

    if timeout_ms is not None and timeout_ms > 0:
        timeout_timer = QTimer(owner)
        timeout_timer.setSingleShot(True)

        def _on_timeout() -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
            setattr(owner, "_busy_task_thread", None)
            setattr(owner, "_busy_task_worker", None)
            setattr(owner, "_busy_task_dialog", None)
            setattr(owner, "_busy_task_relay", None)
            if on_finally is not None:
                on_finally()
            if on_error is not None:
                on_error(
                    RuntimeError("Operation timed out. Check network or API settings and retry.")
                )

        timeout_timer.timeout.connect(_on_timeout)
        timeout_timer.start(int(timeout_ms))

    relay = _BusyTaskRelay(_finish, owner)
    worker.finished.connect(relay.on_finished, Qt.QueuedConnection)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    setattr(owner, "_busy_task_dialog", dialog)
    setattr(owner, "_busy_task_worker", worker)
    setattr(owner, "_busy_task_thread", thread)
    setattr(owner, "_busy_task_relay", relay)
    thread.start()
    return True


class AISettingsDialog(QDialog):
    API_KEY_SOURCE_DIRECT = "direct"
    API_KEY_SOURCE_ENV = "env"
    ACTIVE_INPUT_STYLE = "background: #f8fafc; border: 1px solid #1f7a8c; color: #102a43;"
    INACTIVE_INPUT_STYLE = "background: #edf2f7; border: 1px solid #d9e2ec; color: #7b8794;"
    MODEL_PLACEHOLDER = "__placeholder__"

    def __init__(self, context: AppContext, ui_language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.setWindowTitle(_t(self.ui_language, "AI 设置", "AI Settings"))
        self.resize(720, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        title = QLabel(_t(self.ui_language, "AI 设置", "AI Settings"))
        title.setObjectName("PageTitle")
        title.setStyleSheet("font-size: 18px;")
        note = QLabel(
            _t(
                self.ui_language,
                "这里设置 API Key、模型和界面语言。API Key 支持直接输入或绑定环境变量。",
                "Configure API key, model, and UI language. API key can be direct input or an environment variable.",
            )
        )
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(note)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)

        self.api_key_source_combo = QComboBox()
        self.api_key_source_combo.addItem(
            _t(self.ui_language, "直接输入 API Key", "Direct API Key"),
            self.API_KEY_SOURCE_DIRECT,
        )
        self.api_key_source_combo.addItem(
            _t(self.ui_language, "使用环境变量", "Use Environment Variable"),
            self.API_KEY_SOURCE_ENV,
        )

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("sk-...")

        self.api_key_env_combo = QComboBox()
        self.api_key_env_combo.setEditable(False)
        self.api_key_env_combo.setMinimumContentsLength(28)
        self.api_key_env_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)

        self.model_input = QComboBox()
        self.model_input.setEditable(False)
        self.model_input.setMinimumContentsLength(24)
        self.model_input.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._detected_model_ids: list[str] = []
        self._has_env_var_options = False
        self._cached_api_key_env_var = ""
        self.refresh_models_button = styled_button(
            _t(self.ui_language, "检测并加载模型", "Detect & Load Models"),
            "secondary",
        )
        model_row = QWidget()
        model_row_layout = QHBoxLayout(model_row)
        model_row_layout.setContentsMargins(0, 0, 0, 0)
        model_row_layout.setSpacing(8)
        model_row_layout.addWidget(self.model_input, 1)
        model_row_layout.addWidget(self.refresh_models_button)

        self.ui_language_combo = QComboBox()
        self.ui_language_combo.addItem("🌐 中文 / Chinese", "zh")
        self.ui_language_combo.addItem("🌐 English", "en")
        self.ui_language_combo.setToolTip(
            _t(
                self.ui_language,
                "切换桌面应用界面语言。",
                "Switch the desktop app interface language.",
            )
        )

        form.addRow(_t(self.ui_language, "API Key 来源", "API Key Source"), self.api_key_source_combo)
        form.addRow(_t(self.ui_language, "API Key", "API Key"), self.api_key_input)
        form.addRow(_t(self.ui_language, "环境变量", "Environment Variable"), self.api_key_env_combo)
        form.addRow(_t(self.ui_language, "模型", "Model"), model_row)
        form.addRow(
            _t(self.ui_language, "🌐 语言 / Language", "🌐 Language / 语言"),
            self.ui_language_combo,
        )
        layout.addLayout(form)

        self.model_status_label = QLabel("")
        self.model_status_label.setObjectName("MutedLabel")
        self.model_status_label.setWordWrap(True)
        layout.addWidget(self.model_status_label)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        self.save_button = styled_button(_t(self.ui_language, "保存", "Save"), "primary")
        self.close_button = styled_button(_t(self.ui_language, "关闭", "Close"), "secondary")
        button_row.addStretch(1)
        button_row.addWidget(self.close_button)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        self.close_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self._save)
        self.refresh_models_button.clicked.connect(lambda: self._refresh_models(auto=False))
        self.api_key_source_combo.currentIndexChanged.connect(self._on_key_source_changed)
        self.api_key_input.textChanged.connect(self._on_api_credential_changed)
        self.api_key_env_combo.currentIndexChanged.connect(self._on_env_selection_changed)
        self.api_key_env_combo.currentTextChanged.connect(self._on_env_selection_changed)
        self._load()

    def _load(self) -> None:
        self._ui_loading = True
        settings = self.context.settings.get_openai_settings()
        source = (
            self.API_KEY_SOURCE_ENV
            if settings.api_key_source == self.API_KEY_SOURCE_ENV
            else self.API_KEY_SOURCE_DIRECT
        )
        source_index = self.api_key_source_combo.findData(source)
        self.api_key_source_combo.setCurrentIndex(source_index if source_index >= 0 else 0)

        self._cached_api_key_env_var = str(settings.api_key_env_var or "").strip()
        if source == self.API_KEY_SOURCE_ENV:
            self._populate_api_key_env_options(self._cached_api_key_env_var)
        else:
            self._set_env_combo_waiting_state()
        self.api_key_input.setText(settings.api_key)

        self._detected_model_ids = []
        self._populate_model_options([], "")

        saved_language = self.context.settings.get_ui_language()
        self.ui_language_combo.setCurrentIndex(1 if saved_language == "en" else 0)

        self._ui_loading = False
        self._on_key_source_changed()
        if self._restore_saved_model_selection(settings):
            return
        self._lock_model_selector(
            _t(
                self.ui_language,
                "模型栏默认锁定。填写 API Key 后点击“检测并加载模型”来启用下拉选择。",
                "Model selector is locked by default. After entering API credentials, click 'Detect & Load Models' to enable model dropdown.",
            )
        )

    def _save(self) -> None:
        selected_model = self.model_input.currentText().strip()
        source = self._current_api_key_source()
        env_var = (
            self._current_api_key_env_var()
            if source == self.API_KEY_SOURCE_ENV
            else str(self._cached_api_key_env_var or "").strip()
        )
        if source == self.API_KEY_SOURCE_ENV and not env_var:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "AI 设置", "AI Settings"),
                _t(
                    self.ui_language,
                    "请先选择一个可用环境变量。",
                    "Please choose one available environment variable first.",
                ),
            )
            return

        if not self._detected_model_ids:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "AI 设置", "AI Settings"),
                _t(
                    self.ui_language,
                    "请先点击“检测并加载模型”，并从模型下拉列表中选择一个模型。",
                    "Please click 'Detect & Load Models' first and choose one model from the dropdown list.",
                ),
            )
            return
        if selected_model.casefold() not in {item.casefold() for item in self._detected_model_ids}:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "AI 设置", "AI Settings"),
                _t(
                    self.ui_language,
                    "当前模型不在已加载的模型列表中，请重新检测后选择。",
                    "The selected model is not in the loaded model list. Please detect again and reselect.",
                ),
            )
            return

        self.context.settings.save_openai_settings(
            OpenAISettings(
                api_key=self.api_key_input.text(),
                model=selected_model,
                api_key_source=source,
                api_key_env_var=env_var,
            )
        )
        self.context.settings.save_openai_model_catalog(self._detected_model_ids)

        selected_language = str(self.ui_language_combo.currentData() or "zh")
        self.context.settings.save_ui_language(selected_language)

        effective = self.context.settings.get_effective_openai_settings()
        if source == self.API_KEY_SOURCE_ENV and not effective.api_key.strip():
            message = _t(
                self.ui_language,
                "设置已保存，但当前环境变量没有读取到值。请先在系统里设置该变量，再刷新模型或运行 AI 功能。",
                "Settings saved, but no value was found in the selected environment variable. Set it in your system first, then refresh models or run AI features.",
            )
        else:
            message = _t(
                self.ui_language,
                "AI 设置和界面语言已保存。",
                "AI settings and UI language were saved.",
            )
        QMessageBox.information(
            self,
            _t(self.ui_language, "AI 设置", "AI Settings"),
            message,
        )
        self.accept()

    def _populate_api_key_env_options(self, selected_env_var: str = "") -> None:
        selected = str(selected_env_var or "").strip()
        options = self.context.settings.list_api_key_environment_variables()
        ordered: list[str] = []
        seen: set[str] = set()
        env_name_by_upper = {name.upper(): name for name in os.environ.keys()}

        def push(raw: str) -> None:
            text = str(raw or "").strip()
            if not text:
                return
            key = text.upper()
            if key in seen:
                return
            seen.add(key)
            ordered.append(text)

        for item in options:
            push(item)
        if selected:
            canonical = env_name_by_upper.get(selected.upper())
            if canonical:
                push(canonical)

        self.api_key_env_combo.blockSignals(True)
        self.api_key_env_combo.clear()
        if ordered:
            self._has_env_var_options = True
            for name in ordered:
                self.api_key_env_combo.addItem(name, name)
            if selected:
                selected_upper = selected.upper()
                index = -1
                for offset in range(self.api_key_env_combo.count()):
                    item_text = str(self.api_key_env_combo.itemText(offset) or "")
                    if item_text.upper() == selected_upper:
                        index = offset
                        break
            else:
                index = -1
            if index >= 0:
                self.api_key_env_combo.setCurrentIndex(index)
            else:
                self.api_key_env_combo.setCurrentIndex(0)
            current_env = str(self.api_key_env_combo.currentData() or "").strip()
            if current_env:
                self._cached_api_key_env_var = current_env
        else:
            self._has_env_var_options = False
            self.api_key_env_combo.addItem(
                _t(
                    self.ui_language,
                    "未检测到可选环境变量（请先在系统中设置）",
                    "No environment variable detected (set one in your system first).",
                ),
                "",
            )
            self.api_key_env_combo.setCurrentIndex(0)
        self.api_key_env_combo.blockSignals(False)

    def _set_env_combo_waiting_state(self) -> None:
        self._has_env_var_options = False
        self.api_key_env_combo.blockSignals(True)
        self.api_key_env_combo.clear()
        self.api_key_env_combo.addItem(
            _t(
                self.ui_language,
                "切换到“使用环境变量”后加载可选项",
                "Switch to 'Use Environment Variable' to load options",
            ),
            "",
        )
        self.api_key_env_combo.setCurrentIndex(0)
        self.api_key_env_combo.blockSignals(False)

    def _populate_model_options(self, models: list[str], selected_model: str = "") -> None:
        ordered = self._ordered_model_ids(models, selected_model)

        active = str(selected_model or "").strip()
        self.model_input.blockSignals(True)
        self.model_input.clear()
        if ordered:
            for item in ordered:
                self.model_input.addItem(item, item)
            index = self.model_input.findText(active, Qt.MatchFixedString)
            if index >= 0:
                self.model_input.setCurrentIndex(index)
            else:
                self.model_input.setCurrentIndex(0)
        else:
            self.model_input.addItem(
                _t(self.ui_language, "请先检测并加载模型", "Detect models first"),
                self.MODEL_PLACEHOLDER,
            )
            self.model_input.setCurrentIndex(0)
        self.model_input.blockSignals(False)

    @staticmethod
    def _ordered_model_ids(models: list[str], selected_model: str = "") -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()

        def push(raw: str) -> None:
            text = str(raw or "").strip()
            if not text:
                return
            key = text.casefold()
            if key in seen:
                return
            seen.add(key)
            ordered.append(text)

        for item in models:
            push(item)
        if selected_model:
            push(selected_model)
        ordered.sort(key=lambda item: item.casefold())
        return ordered

    def _restore_saved_model_selection(self, settings: OpenAISettings) -> bool:
        saved_model = str(settings.model or "").strip()
        saved_catalog = self.context.settings.get_openai_model_catalog()
        if saved_model and saved_model.casefold() not in {item.casefold() for item in saved_catalog}:
            saved_catalog = [*saved_catalog, saved_model]
        if not saved_catalog:
            return False

        saved_catalog = self._ordered_model_ids(saved_catalog)
        preferred = saved_model or self._default_low_cost_model(saved_catalog)
        self._detected_model_ids = list(saved_catalog)
        self._populate_model_options(saved_catalog, preferred)
        self._set_model_selector_enabled(True)
        self.model_status_label.setText(
            _t(
                self.ui_language,
                f"已恢复上次保存的模型设置：{preferred}。如需刷新完整模型列表，可点击“检测并加载模型”；当前模型可用性会在工作台后台自动校验。",
                f"Restored the previously saved model selection: {preferred}. Click 'Detect & Load Models' to refresh the full model list; model availability is checked automatically in the workspace.",
            )
        )
        return True

    def _set_model_selector_enabled(self, enabled: bool) -> None:
        self.model_input.setEnabled(enabled)
        self.model_input.setEditable(False)
        self.model_input.setStyleSheet(self.ACTIVE_INPUT_STYLE if enabled else self.INACTIVE_INPUT_STYLE)

    def _lock_model_selector(self, status_text: str = "") -> None:
        if status_text:
            self.model_status_label.setText(status_text)
        self._set_model_selector_enabled(False)

    def _on_api_credential_changed(self, _: str = "") -> None:
        if getattr(self, "_ui_loading", False):
            return
        self._detected_model_ids = []
        self._populate_model_options([], "")
        self._lock_model_selector(
            _t(
                self.ui_language,
                "API 凭据已变更。请点击“检测并加载模型”重新获取可用模型。",
                "API credentials changed. Click 'Detect & Load Models' to reload available models.",
            )
        )

    def _on_env_selection_changed(self, _: object = "") -> None:
        selected = str(self.api_key_env_combo.currentData() or "").strip()
        if selected:
            self._cached_api_key_env_var = selected
        self._on_api_credential_changed()

    @staticmethod
    def _default_low_cost_model(models: list[str]) -> str:
        if not models:
            return "gpt-5"

        def score(name: str) -> tuple[int, int, str]:
            text = str(name or "").strip()
            lower = text.casefold()
            cost_rank = 100
            if "nano" in lower:
                cost_rank = 0
            elif "mini" in lower:
                cost_rank = 1
            elif "small" in lower or "lite" in lower:
                cost_rank = 2
            elif "flash" in lower:
                cost_rank = 3
            elif "codex" in lower:
                cost_rank = 6
            elif "gpt-5" in lower:
                cost_rank = 7
            elif "gpt-4o" in lower:
                cost_rank = 8
            elif "gpt-4.1" in lower:
                cost_rank = 9
            return (cost_rank, len(text), lower)

        return min(models, key=score)

    def _current_api_key_source(self) -> str:
        raw = str(self.api_key_source_combo.currentData() or self.API_KEY_SOURCE_DIRECT).strip().lower()
        if raw == self.API_KEY_SOURCE_ENV:
            return self.API_KEY_SOURCE_ENV
        return self.API_KEY_SOURCE_DIRECT

    def _current_api_key_env_var(self) -> str:
        if self._current_api_key_source() != self.API_KEY_SOURCE_ENV:
            return str(self._cached_api_key_env_var or "").strip()
        return str(self.api_key_env_combo.currentData() or "").strip()

    def _resolve_api_key_for_actions(self) -> tuple[str, str]:
        source = self._current_api_key_source()
        if source == self.API_KEY_SOURCE_DIRECT:
            key = self.api_key_input.text().strip()
            if key:
                return key, ""
            return (
                "",
                _t(
                    self.ui_language,
                    "当前是直接输入模式，但 API Key 为空。",
                    "Direct mode is selected, but API key is empty.",
                ),
            )

        env_name = self._current_api_key_env_var()
        if not env_name:
            return (
                "",
                _t(
                    self.ui_language,
                    "当前没有可用环境变量可供选择。",
                    "No available environment variable can be selected right now.",
                ),
            )
        env_value = os.getenv(env_name, "").strip()
        if not env_value:
            env_name_upper = env_name.upper()
            for raw_name, raw_value in os.environ.items():
                if raw_name.upper() == env_name_upper:
                    env_value = str(raw_value or "").strip()
                    break
        if env_value:
            return env_value, ""
        return (
            "",
            _t(
                self.ui_language,
                f"环境变量 {env_name} 当前为空或不存在。",
                f"Environment variable {env_name} is empty or not set.",
            ),
        )

    def _on_key_source_changed(self) -> None:
        source = self._current_api_key_source()
        direct_mode = source == self.API_KEY_SOURCE_DIRECT
        if direct_mode:
            current_env = str(self.api_key_env_combo.currentData() or "").strip()
            if current_env:
                self._cached_api_key_env_var = current_env
            self._set_env_combo_waiting_state()
        else:
            self._populate_api_key_env_options(self._cached_api_key_env_var)
        self.api_key_input.setEnabled(direct_mode)
        self.api_key_env_combo.setEnabled((not direct_mode) and self._has_env_var_options)
        self.api_key_input.setStyleSheet(self.ACTIVE_INPUT_STYLE if direct_mode else self.INACTIVE_INPUT_STYLE)
        self.api_key_env_combo.setStyleSheet(self.INACTIVE_INPUT_STYLE if direct_mode else self.ACTIVE_INPUT_STYLE)
        if direct_mode:
            self.api_key_input.setPlaceholderText("sk-...")
            self.api_key_input.setFocus(Qt.OtherFocusReason)
        else:
            self.api_key_input.setPlaceholderText(
                _t(
                    self.ui_language,
                    "当前使用环境变量读取，不会使用这里的输入值。",
                    "Environment-variable mode is active; this input is not used.",
                )
            )
            if self._has_env_var_options:
                self.api_key_env_combo.setFocus(Qt.OtherFocusReason)
        self._on_api_credential_changed()

    def _refresh_models(self, auto: bool = False, preserve_existing: bool = False) -> None:
        restored_models = list(self._detected_model_ids)
        restored_model = self.model_input.currentText().strip()
        api_key, api_key_error = self._resolve_api_key_for_actions()
        if not api_key:
            if preserve_existing and restored_models:
                self._set_model_selector_enabled(True)
                self.model_status_label.setText(
                    _t(
                        self.ui_language,
                        f"未能完成本次验证：{api_key_error}。当前先保留上次保存的模型选择：{restored_model or self._default_low_cost_model(restored_models)}。",
                        f"Could not revalidate now: {api_key_error}. Keeping the previously saved model selection for now: {restored_model or self._default_low_cost_model(restored_models)}.",
                    )
                )
                return
            self._lock_model_selector(
                _t(
                    self.ui_language,
                    f"无法刷新模型：{api_key_error}",
                    f"Cannot refresh models: {api_key_error}",
                )
            )
            if not auto:
                QMessageBox.information(
                    self,
                    _t(self.ui_language, "AI 设置", "AI Settings"),
                    api_key_error,
                )
            return

        current_model = self.model_input.currentText().strip()
        base_url = self.context.settings.get_openai_base_url()
        stored_model = self.context.settings.get_openai_settings().model.strip()
        self.refresh_models_button.setEnabled(False)
        request_title = _t(self.ui_language, "AI 设置", "AI Settings")
        request_message = _t(
            self.ui_language,
            "正在加载模型列表，请稍候...",
            "Loading model list, please wait...",
        )
        model_list_timeout_seconds = 12 if auto else 20

        def _task() -> dict[str, Any]:
            result = fetch_available_models(
                api_key=api_key,
                api_base_url=base_url,
                timeout_seconds=model_list_timeout_seconds,
            )
            return {"result": result}

        def _on_success(payload: Any) -> None:
            data = payload if isinstance(payload, dict) else {}
            result = data.get("result")
            if result is not None and getattr(result, "models", None):
                loaded_models = self._ordered_model_ids(result.models)
                current_available = {item.casefold() for item in loaded_models}
                fallback_default = loaded_models[0] if loaded_models else ""
                preferred = ""
                for candidate in (current_model, stored_model):
                    text = str(candidate or "").strip()
                    if text and text.casefold() in current_available:
                        preferred = text
                        break
                if not preferred:
                    preferred = fallback_default
                self._detected_model_ids = loaded_models
                self._populate_model_options(loaded_models, preferred)
                self.context.settings.save_openai_model_catalog(loaded_models)
                status_text = _t(
                    self.ui_language,
                    f"模型列表已更新：共加载 {len(loaded_models)} 个模型，已按字母顺序排序。当前选择：{preferred or '未选择'}。模型可用性会在工作台后台自动校验。",
                    f"Model list updated: {len(loaded_models)} models loaded and sorted alphabetically. Current selection: {preferred or 'none'}. Model availability is checked automatically in the workspace.",
                )
                self.model_status_label.setText(status_text)
                self._set_model_selector_enabled(True)
                return

            error_text = ""
            if result is not None:
                error_text = str(getattr(result, "error", "") or "")
            if preserve_existing and restored_models:
                self._detected_model_ids = restored_models
                self._populate_model_options(restored_models, restored_model)
                self._set_model_selector_enabled(True)
                self.model_status_label.setText(
                    _t(
                        self.ui_language,
                        f"本次未能获取模型列表：{error_text or '未知错误'}。当前先保留上次保存的模型选择。",
                        f"Could not load the model list this time: {error_text or 'unknown error'}. Keeping the previously saved model selection for now.",
                    )
                )
                return
            self._detected_model_ids = []
            self._populate_model_options([], "")
            self._lock_model_selector(
                _t(
                    self.ui_language,
                    f"模型列表加载失败：{error_text or '未知错误'}",
                    f"Model list loading failed: {error_text or 'unknown error'}",
                )
            )
            if not auto:
                QMessageBox.warning(
                    self,
                    request_title,
                    _t(
                        self.ui_language,
                        "未获取到模型列表，模型栏保持锁定。请检查 API 凭据后重试。",
                        "Could not load the model list. Model selector remains locked. Check API credentials and try again.",
                    ),
                )

        def _on_error(exc: Exception) -> None:
            if preserve_existing and restored_models:
                self._detected_model_ids = restored_models
                self._populate_model_options(restored_models, restored_model)
                self._set_model_selector_enabled(True)
                self.model_status_label.setText(
                    _t(
                        self.ui_language,
                        f"本次加载模型列表失败：{exc}。当前先保留上次保存的模型选择。",
                        f"Loading the model list failed this time: {exc}. Keeping the previously saved model selection for now.",
                    )
                )
                return
            self._detected_model_ids = []
            self._populate_model_options([], "")
            self._lock_model_selector(
                _t(
                    self.ui_language,
                    f"模型列表加载失败：{exc}",
                    f"Model list loading failed: {exc}",
                )
            )
            if not auto:
                QMessageBox.warning(
                    self,
                    request_title,
                    str(exc),
                )

        def _on_finally() -> None:
            self.refresh_models_button.setEnabled(True)

        started = run_busy_task(
            self,
            title=request_title,
            message=request_message,
            task=_task,
            on_success=_on_success,
            on_error=_on_error,
            on_finally=_on_finally,
        )
        if not started:
            self.refresh_models_button.setEnabled(True)


class ManualRoleInputDialog(QDialog):
    def __init__(self, ui_language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.setWindowTitle(_t(self.ui_language, "手动添加岗位", "Add Role Manually"))
        self.resize(620, 340)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        intro = QLabel(
            _t(
                self.ui_language,
                "请输入岗位名称和大致说明。提交后会由 AI 自动补全更详细的岗位说明。",
                "Enter role name and rough notes. After submit, AI will enrich it into a detailed role description.",
            )
        )
        intro.setObjectName("MutedLabel")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)

        self.role_name_input = QLineEdit()
        self.role_name_input.setPlaceholderText(
            _t(
                self.ui_language,
                "例如：Systems Integration & Test Engineer (HIL/SIL)",
                "For example: Systems Integration & Test Engineer (HIL/SIL)",
            )
        )
        self.rough_description_input = QPlainTextEdit()
        self.rough_description_input.setMinimumHeight(150)
        self.rough_description_input.setPlaceholderText(
            _t(
                self.ui_language,
                "例如：偏向系统集成、测试自动化、需求可追溯，最好贴近我的专业背景。",
                "For example: Focus on systems integration, test automation, and requirements traceability, aligned with my background.",
            )
        )
        form.addRow(_t(self.ui_language, "岗位名称", "Role Name"), self.role_name_input)
        form.addRow(_t(self.ui_language, "大致说明（可选）", "Rough Notes (Optional)"), self.rough_description_input)
        layout.addLayout(form)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        self.cancel_button = styled_button(_t(self.ui_language, "取消", "Cancel"), "secondary")
        self.submit_button = styled_button(_t(self.ui_language, "提交并补全", "Submit & Enrich"), "primary")
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.submit_button)
        layout.addLayout(actions)

        self.cancel_button.clicked.connect(self.reject)
        self.submit_button.clicked.connect(self.accept)

    def values(self) -> tuple[str, str]:
        return (
            self.role_name_input.text().strip(),
            self.rough_description_input.toPlainText().strip(),
        )


def scope_label(scope_profile: str, language: str = "zh") -> str:
    normalized_language = "en" if language == "en" else "zh"
    return {
        "hydrogen_mainline": _t(normalized_language, "氢能主线", "Hydrogen Mainline"),
        "adjacent_mbse": _t(
            normalized_language,
            "副线：MBSE / 系统验证 / 技术接口",
            "Adjacent: MBSE / V&V / Technical Interface",
        ),
    }.get(scope_profile, scope_profile or _t(normalized_language, "未设置", "Unset"))


class CandidateForm(QWidget):
    BASE_LOCATION_FIXED_TYPE = "city"
    PREFERRED_LOCATION_TYPES = ("global", "remote", "region", "country", "city")

    def __init__(self, save_button_text: str = "保存基础信息", ui_language: str = "zh") -> None:
        super().__init__()
        self.ui_language = "en" if ui_language == "en" else "zh"
        self._target_directions_cached = ""
        self._preferred_location_items: list[dict[str, str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.meta_label = QLabel(_t(self.ui_language, "请选择或创建一个求职者。", "Select or create a candidate first."))
        self.meta_label.setObjectName("MutedLabel")
        layout.addWidget(self.meta_label)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)

        self.name_input = QLineEdit()
        self.email_input = QLineEdit()

        self.base_location_input = QComboBox()
        self.base_location_input.setEditable(False)
        self.base_location_input.setMinimumContentsLength(24)
        self.base_location_input.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        base_location_row = QWidget()
        base_location_row_layout = QHBoxLayout(base_location_row)
        base_location_row_layout.setContentsMargins(0, 0, 0, 0)
        base_location_row_layout.setSpacing(8)
        base_location_row_layout.addWidget(self.base_location_input, 1)
        self.base_location_warning_label = QLabel("")
        self.base_location_warning_label.setObjectName("MutedLabel")
        self.base_location_warning_label.setStyleSheet("color: #b45309;")
        self.base_location_warning_label.setWordWrap(True)
        base_location_wrapper = QWidget()
        base_location_wrapper_layout = QVBoxLayout(base_location_wrapper)
        base_location_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        base_location_wrapper_layout.setSpacing(4)
        base_location_wrapper_layout.addWidget(base_location_row)
        base_location_wrapper_layout.addWidget(self.base_location_warning_label)

        self.preferred_location_type_combo = QComboBox()
        self._populate_location_type_combo(self.preferred_location_type_combo, self.PREFERRED_LOCATION_TYPES)
        self.preferred_location_input = QComboBox()
        self.preferred_location_input.setEditable(False)
        self.preferred_location_input.setMinimumContentsLength(24)
        self.preferred_location_input.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.add_preferred_location_button = styled_button(
            _t(self.ui_language, "添加地点", "Add"),
            "secondary",
        )
        self.remove_preferred_location_button = styled_button(
            _t(self.ui_language, "删除选中", "Remove Selected"),
            "danger",
        )
        preferred_control_row = QWidget()
        preferred_control_row_layout = QHBoxLayout(preferred_control_row)
        preferred_control_row_layout.setContentsMargins(0, 0, 0, 0)
        preferred_control_row_layout.setSpacing(8)
        preferred_control_row_layout.addWidget(self.preferred_location_type_combo, 0)
        preferred_control_row_layout.addWidget(self.preferred_location_input, 1)
        preferred_control_row_layout.addWidget(self.add_preferred_location_button, 0)
        preferred_control_row_layout.addWidget(self.remove_preferred_location_button, 0)
        self.preferred_locations_list = QListWidget()
        self.preferred_locations_list.setMinimumHeight(96)
        self.preferred_locations_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.preferred_locations_warning_label = QLabel(
            _t(
                self.ui_language,
                "可添加多个地点标签：Global / Region / Country / City / Remote。",
                "You can add multiple location tags: Global / Region / Country / City / Remote.",
            )
        )
        self.preferred_locations_warning_label.setObjectName("MutedLabel")
        self.preferred_locations_warning_label.setStyleSheet("color: #b45309;")
        self.preferred_locations_warning_label.setWordWrap(True)
        preferred_wrapper = QWidget()
        preferred_wrapper_layout = QVBoxLayout(preferred_wrapper)
        preferred_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        preferred_wrapper_layout.setSpacing(6)
        preferred_wrapper_layout.addWidget(preferred_control_row)
        preferred_wrapper_layout.addWidget(self.preferred_locations_list)
        preferred_wrapper_layout.addWidget(self.preferred_locations_warning_label)

        self.resume_input = QLineEdit()
        self.choose_resume_button = styled_button(
            _t(self.ui_language, "选择简历", "Select Resume"),
            "secondary",
        )
        resume_row = QWidget()
        resume_row_layout = QHBoxLayout(resume_row)
        resume_row_layout.setContentsMargins(0, 0, 0, 0)
        resume_row_layout.setSpacing(8)
        resume_row_layout.addWidget(self.resume_input, 1)
        resume_row_layout.addWidget(self.choose_resume_button)

        self.notes_input = QPlainTextEdit()
        self.notes_input.setPlaceholderText(
            _t(
                self.ui_language,
                "例如：过往工作经历、研究方向、行业专长、核心项目、技术强项，以及你希望继续深耕的主题。",
                "For example: work history, research focus, industry expertise, core projects, technical strengths, and the themes you want to keep pursuing.",
            )
        )
        self.notes_input.setMinimumHeight(110)

        form.addRow(_t(self.ui_language, "姓名", "Name"), self.name_input)
        form.addRow(_t(self.ui_language, "邮箱", "Email"), self.email_input)
        form.addRow(_t(self.ui_language, "当前所在地", "Current Location"), base_location_wrapper)
        form.addRow(_t(self.ui_language, "希望找工作的地点", "Preferred Locations"), preferred_wrapper)
        form.addRow(_t(self.ui_language, "简历路径", "Resume Path"), resume_row)
        form.addRow(_t(self.ui_language, "职业背景 / 专业摘要", "Professional Background / Summary"), self.notes_input)
        layout.addLayout(form)

        self.actions_row = QHBoxLayout()
        self.actions_row.setContentsMargins(0, 0, 0, 0)
        self.actions_row.setSpacing(8)
        self.save_button = styled_button(save_button_text, "primary")
        self.actions_row.addWidget(self.save_button)
        self.actions_row.addStretch(1)
        layout.addLayout(self.actions_row)

        self.choose_resume_button.clicked.connect(self._choose_resume)
        self.preferred_location_type_combo.currentIndexChanged.connect(self._on_preferred_location_type_changed)
        self.add_preferred_location_button.clicked.connect(self._add_preferred_location)
        self.remove_preferred_location_button.clicked.connect(self._remove_selected_preferred_locations)
        self._set_location_suggestions(self.base_location_input, self.BASE_LOCATION_FIXED_TYPE)
        self._on_preferred_location_type_changed()

    def _location_type_label(self, location_type: str) -> str:
        return {
            "global": _t(self.ui_language, "全球", "Global"),
            "remote": _t(self.ui_language, "远程", "Remote"),
            "region": _t(self.ui_language, "大区", "Region"),
            "country": _t(self.ui_language, "国家", "Country"),
            "city": _t(self.ui_language, "城市", "City"),
        }.get(location_type, location_type)

    def _populate_location_type_combo(self, combo: QComboBox, location_types: tuple[str, ...]) -> None:
        combo.clear()
        for location_type in location_types:
            combo.addItem(self._location_type_label(location_type), location_type)

    def _set_location_suggestions(
        self,
        combo: QComboBox,
        location_type: str,
        preserve_text: str | None = None,
    ) -> None:
        editable = combo.isEditable()
        current_text = combo.currentText() or ""
        keep_text = str(preserve_text if preserve_text is not None else current_text).strip()
        suggestions = location_type_suggestions(location_type)
        combo.blockSignals(True)
        combo.clear()
        if suggestions:
            combo.addItems(suggestions)
        if keep_text:
            index = combo.findText(keep_text, Qt.MatchFixedString)
            if index >= 0:
                combo.setCurrentIndex(index)
            else:
                if preserve_text is not None:
                    combo.addItem(keep_text, keep_text)
                    fallback_index = combo.findText(keep_text, Qt.MatchFixedString)
                    combo.setCurrentIndex(fallback_index if fallback_index >= 0 else 0)
                else:
                    combo.setCurrentIndex(-1)
                    if editable:
                        combo.setEditText("")
        else:
            combo.setCurrentIndex(-1)
            if editable:
                combo.setEditText("")
        combo.blockSignals(False)

    def _selected_location_type(self, combo: QComboBox) -> str:
        return str(combo.currentData() or "country").strip().lower()

    def _warning_messages(self, warning_codes: list[str]) -> list[str]:
        mapping = {
            "empty_label": _t(
                self.ui_language,
                "地点为空，已忽略该输入。",
                "Location is empty and has been ignored.",
            ),
            "unknown_country": _t(
                self.ui_language,
                "国家未标准化，先按原文保留。",
                "Country is not normalized; kept as raw text for now.",
            ),
            "unknown_city": _t(
                self.ui_language,
                "城市未标准化，先按原文保留。",
                "City is not normalized; kept as raw text for now.",
            ),
            "city_without_country": _t(
                self.ui_language,
                "城市未带国家，建议补充“城市, 国家”。",
                "City has no country. Recommend 'City, Country'.",
            ),
            "unknown_region": _t(
                self.ui_language,
                "大区未标准化，先按原文保留。",
                "Region is not normalized; kept as raw text for now.",
            ),
        }
        messages: list[str] = []
        seen: set[str] = set()
        for code in warning_codes:
            text = mapping.get(str(code or "").strip())
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            messages.append(text)
        return messages

    def _set_base_location_warning(self, warning_codes: list[str]) -> None:
        messages = self._warning_messages(warning_codes)
        if messages:
            self.base_location_warning_label.setText(_t(self.ui_language, "提示：", "Note: ") + "；".join(messages))
            return
        self.base_location_warning_label.setText("")

    def _set_preferred_location_warning(self, warning_codes: list[str]) -> None:
        messages = self._warning_messages(warning_codes)
        if messages:
            self.preferred_locations_warning_label.setText(
                _t(self.ui_language, "提示：", "Note: ") + "；".join(messages)
            )
            return
        self.preferred_locations_warning_label.setText(
            _t(
                self.ui_language,
                "可添加多个地点标签：Global / Region / Country / City / Remote。",
                "You can add multiple location tags: Global / Region / Country / City / Remote.",
            )
        )

    def _on_preferred_location_type_changed(self) -> None:
        location_type = self._selected_location_type(self.preferred_location_type_combo)
        self._set_location_suggestions(self.preferred_location_input, location_type)

    def _set_base_location_entry(self, entry: dict[str, str] | None) -> None:
        if entry is None:
            self._set_location_suggestions(self.base_location_input, self.BASE_LOCATION_FIXED_TYPE, preserve_text="")
            return
        preserve_value = location_entry_display(entry)
        if not str(entry.get("city") or "").strip():
            preserve_value = ""
        self._set_location_suggestions(
            self.base_location_input,
            self.BASE_LOCATION_FIXED_TYPE,
            preserve_text=preserve_value,
        )

    def _render_preferred_location_items(self) -> None:
        self._preferred_location_items = dedup_location_entries(self._preferred_location_items)
        self.preferred_locations_list.clear()
        for location_entry in self._preferred_location_items:
            location_type = str(location_entry.get("type") or "").strip().lower()
            display_text = location_entry_display(location_entry)
            item = QListWidgetItem(f"{self._location_type_label(location_type)} · {display_text}")
            self.preferred_locations_list.addItem(item)

    def _add_preferred_location(self) -> None:
        location_type = self._selected_location_type(self.preferred_location_type_combo)
        raw_value = self.preferred_location_input.currentText().strip()
        entry, warning_codes = normalize_location_entry(location_type, raw_value)
        self._set_preferred_location_warning(warning_codes)
        if entry is None:
            return
        self._preferred_location_items.append(entry)
        self._render_preferred_location_items()
        self._set_location_suggestions(self.preferred_location_input, location_type, preserve_text="")

    def _remove_selected_preferred_locations(self) -> None:
        selected_rows = sorted(
            {self.preferred_locations_list.row(item) for item in self.preferred_locations_list.selectedItems()},
            reverse=True,
        )
        if not selected_rows and self.preferred_locations_list.currentRow() >= 0:
            selected_rows = [self.preferred_locations_list.currentRow()]
        if not selected_rows:
            return
        for row in selected_rows:
            if 0 <= row < len(self._preferred_location_items):
                self._preferred_location_items.pop(row)
        self._render_preferred_location_items()
        self._set_preferred_location_warning([])

    def load_record(self, record: CandidateRecord) -> None:
        self.name_input.setText(record.name)
        self.email_input.setText(record.email)
        self._set_base_location_entry(
            decode_base_location_struct(
                raw_struct=record.base_location_struct,
                fallback_text=record.base_location,
            )
        )
        self._preferred_location_items = decode_preferred_locations_struct(
            raw_struct=record.preferred_locations_struct,
            fallback_text=record.preferred_locations,
        )
        self._render_preferred_location_items()
        self._set_base_location_warning([])
        self._set_preferred_location_warning([])
        self._target_directions_cached = record.target_directions
        self.resume_input.setText(record.active_resume_path)
        self.notes_input.setPlainText(record.notes)
        if self.ui_language == "en":
            self.meta_label.setText(
                f"Candidate ID: {record.candidate_id}    Created: {record.created_at or '-'}    Updated: {record.updated_at or '-'}"
            )
        else:
            self.meta_label.setText(
                f"求职者 ID: {record.candidate_id}    创建时间: {record.created_at or '-'}    最近更新: {record.updated_at or '-'}"
            )
        self.set_form_enabled(True)

    def clear(self, message: str | None = None) -> None:
        self.name_input.clear()
        self.email_input.clear()
        self._set_base_location_entry(None)
        self._preferred_location_items = []
        self._on_preferred_location_type_changed()
        self._render_preferred_location_items()
        self._set_base_location_warning([])
        self._set_preferred_location_warning([])
        self._target_directions_cached = ""
        self.resume_input.clear()
        self.notes_input.clear()
        if message is None:
            message = _t(self.ui_language, "请选择或创建一个求职者。", "Select or create a candidate first.")
        self.meta_label.setText(message)

    def to_record(self, candidate_id: int | None) -> CandidateRecord:
        base_entry, base_warning_codes = normalize_location_entry(
            self.BASE_LOCATION_FIXED_TYPE,
            self.base_location_input.currentText().strip(),
        )
        self._set_base_location_warning(base_warning_codes)
        base_location_text = location_entry_display(base_entry) if base_entry is not None else ""
        base_location_struct = encode_base_location_struct(base_entry)

        pending_preferred_text = (
            self.preferred_location_input.currentText().strip()
            if self.preferred_location_input.isEditable()
            else ""
        )
        pending_warning_codes: list[str] = []
        if pending_preferred_text:
            pending_entry, pending_warning_codes = normalize_location_entry(
                self._selected_location_type(self.preferred_location_type_combo),
                pending_preferred_text,
            )
            if pending_entry is not None:
                self._preferred_location_items.append(pending_entry)
                self._render_preferred_location_items()
                self._set_location_suggestions(
                    self.preferred_location_input,
                    self._selected_location_type(self.preferred_location_type_combo),
                    preserve_text="",
                )
        self._set_preferred_location_warning(pending_warning_codes)
        preferred_location_items = dedup_location_entries(self._preferred_location_items)
        preferred_locations_text = preferred_locations_plain_text(preferred_location_items)
        preferred_locations_struct = encode_preferred_locations_struct(preferred_location_items)

        return CandidateRecord(
            candidate_id=candidate_id,
            name=self.name_input.text(),
            email=self.email_input.text(),
            base_location=base_location_text,
            preferred_locations=preferred_locations_text,
            target_directions=self._target_directions_cached,
            notes=self.notes_input.toPlainText(),
            active_resume_path=self.resume_input.text(),
            created_at="",
            updated_at="",
            base_location_struct=base_location_struct,
            preferred_locations_struct=preferred_locations_struct,
        )

    def set_form_enabled(self, enabled: bool) -> None:
        for widget in (
            self.name_input,
            self.email_input,
            self.base_location_input,
            self.preferred_location_type_combo,
            self.preferred_location_input,
            self.add_preferred_location_button,
            self.remove_preferred_location_button,
            self.preferred_locations_list,
            self.resume_input,
            self.choose_resume_button,
            self.notes_input,
            self.save_button,
        ):
            widget.setEnabled(enabled)

    def _choose_resume(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            _t(self.ui_language, "选择简历文件", "Select Resume File"),
            "",
            "Resume Files (*.pdf *.md *.txt *.docx);;All Files (*.*)",
        )
        if file_path:
            self.resume_input.setText(file_path)


class CandidateDirectoryPage(QWidget):
    def __init__(
        self,
        context: AppContext,
        ui_language: str = "zh",
        on_data_changed: Callable[[], None] | None = None,
        on_candidate_selected: Callable[[int | None], None] | None = None,
        on_open_workspace: Callable[[], None] | None = None,
        on_ui_language_changed: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.context = context
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.on_data_changed = on_data_changed
        self.on_candidate_selected = on_candidate_selected
        self.on_open_workspace = on_open_workspace
        self.on_ui_language_changed = on_ui_language_changed
        self.records: list[CandidateRecord] = []
        self.summaries_by_id: dict[int, CandidateSummary] = {}
        self.current_candidate_id: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        layout.addWidget(
            make_page_title(
                _t(self.ui_language, "选择求职者", "Select Candidate"),
                _t(
                    self.ui_language,
                    "先选择一个已有求职者，或者新建一个人。进入之后就是这个人的专属工作台。",
                    "Select an existing candidate or create a new one, then enter the dedicated workspace.",
                ),
            )
        )

        left_card = make_card()
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(12)

        list_hint = QLabel(
            _t(
                self.ui_language,
                "选择一个求职者，然后进入这个人的工作台。",
                "Pick a candidate, then open this candidate's workspace.",
            )
        )
        list_hint.setObjectName("MutedLabel")
        list_hint.setWordWrap(True)
        left_layout.addWidget(list_hint)

        self.candidate_list = QListWidget()
        self.candidate_list.setObjectName("EntityList")
        left_layout.addWidget(self.candidate_list, 1)

        right_card = make_card()
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(22, 22, 22, 22)
        right_layout.setSpacing(12)

        action_title = QLabel(_t(self.ui_language, "操作", "Actions"))
        action_title.setObjectName("PageTitle")
        action_title.setStyleSheet("font-size: 18px;")
        action_note = QLabel(
            _t(
                self.ui_language,
                "启动页只负责选人。详细信息和简历编辑，都放到进入工作台之后的第一步里。",
                "This page is only for candidate selection. Details and resume editing are in step 1.",
            )
        )
        action_note.setObjectName("PageSubtitle")
        action_note.setWordWrap(True)
        right_layout.addWidget(action_title)
        right_layout.addWidget(action_note)

        language_row = QHBoxLayout()
        language_row.setContentsMargins(0, 0, 0, 0)
        language_row.setSpacing(8)
        self.language_label = QLabel(_t(self.ui_language, "🌐 语言 / Language", "🌐 Language / 语言"))
        self.language_label.setObjectName("MutedLabel")
        self.language_combo = QComboBox()
        self.language_combo.addItem("🌐 中文 / Chinese", "zh")
        self.language_combo.addItem("🌐 English", "en")
        self.language_combo.setToolTip(
            _t(
                self.ui_language,
                "切换桌面应用界面语言。",
                "Switch the desktop app interface language.",
            )
        )
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(1 if self.ui_language == "en" else 0)
        self.language_combo.blockSignals(False)
        language_row.addWidget(self.language_label)
        language_row.addWidget(self.language_combo)
        language_row.addStretch(1)
        right_layout.addLayout(language_row)

        self.open_workspace_button = styled_button(
            _t(self.ui_language, "进入这个人的工作台", "Open Candidate Workspace"),
            "primary",
        )
        self.new_button = styled_button(_t(self.ui_language, "新建求职者", "New Candidate"), "secondary")
        self.delete_button = styled_button(_t(self.ui_language, "删除这个人", "Delete Candidate"), "danger")
        right_layout.addWidget(self.open_workspace_button)
        right_layout.addWidget(self.new_button)
        right_layout.addWidget(self.delete_button)
        right_layout.addStretch(1)

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(16)
        content_row.addWidget(left_card, 2)
        content_row.addWidget(right_card, 1)
        layout.addLayout(content_row, 1)

        self.new_button.clicked.connect(self._new_candidate)
        self.delete_button.clicked.connect(self._delete_candidate)
        self.open_workspace_button.clicked.connect(self._open_workspace)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        self.candidate_list.currentItemChanged.connect(self._on_candidate_selected)
        self.candidate_list.itemDoubleClicked.connect(lambda _: self._open_workspace())
        self._update_action_state()

    def reload(self, select_candidate_id: int | None = None) -> None:
        self.records = self.context.candidates.list_records()
        self.summaries_by_id = {
            summary.candidate_id: summary for summary in self.context.candidates.list_summaries()
        }
        preserve_id = select_candidate_id if select_candidate_id is not None else self.current_candidate_id

        self.candidate_list.blockSignals(True)
        self.candidate_list.clear()
        target_row = None
        for row_index, record in enumerate(self.records):
            summary = self.summaries_by_id.get(record.candidate_id or -1)
            resume_name = Path(record.active_resume_path).name if record.active_resume_path else _t(
                self.ui_language,
                "未设置简历",
                "No resume",
            )
            profile_count = summary.profile_count if summary is not None else 0
            role_text = _t(self.ui_language, f"{profile_count} 个目标岗位", f"{profile_count} roles")
            item = QListWidgetItem(f"{record.name}\n{resume_name}    ·    {role_text}")
            item.setData(Qt.UserRole, record.candidate_id)
            self.candidate_list.addItem(item)
            if preserve_id == record.candidate_id:
                target_row = row_index
        self.candidate_list.blockSignals(False)

        if target_row is not None:
            self.candidate_list.setCurrentRow(target_row)
            return

        if self.records:
            self.candidate_list.setCurrentRow(0)
            return

        self.current_candidate_id = None
        self.candidate_list.clearSelection()
        self._update_action_state()

    def _find_record(self, candidate_id: int | None) -> CandidateRecord | None:
        for record in self.records:
            if record.candidate_id == candidate_id:
                return record
        return None

    def _selected_candidate_id_from_list(self) -> int | None:
        current = self.candidate_list.currentItem()
        if current is None:
            return None
        candidate_id = current.data(Qt.UserRole)
        return int(candidate_id) if candidate_id is not None else None

    def _on_candidate_selected(self, current: QListWidgetItem | None, _: QListWidgetItem | None) -> None:
        if current is None:
            self.current_candidate_id = None
            self._update_action_state()
            if self.on_candidate_selected:
                self.on_candidate_selected(None)
            return

        candidate_id = current.data(Qt.UserRole)
        self.current_candidate_id = int(candidate_id) if candidate_id is not None else None
        self._update_action_state()
        if self.on_candidate_selected:
            self.on_candidate_selected(self.current_candidate_id)

    def _new_candidate(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            _t(self.ui_language, "新建求职者", "New Candidate"),
            _t(self.ui_language, "请输入这个求职者的名称：", "Please enter the candidate name:"),
        )
        if not ok:
            return
        if not name.strip():
            QMessageBox.warning(
                self,
                _t(self.ui_language, "新建求职者", "New Candidate"),
                _t(self.ui_language, "名称不能为空。", "Name cannot be empty."),
            )
            return

        candidate_id = self.context.candidates.save(
            CandidateRecord(
                candidate_id=None,
                name=name.strip(),
                email="",
                base_location="",
                preferred_locations="",
                target_directions="",
                notes="",
                active_resume_path="",
                created_at="",
                updated_at="",
            )
        )

        self.current_candidate_id = candidate_id
        self.reload(select_candidate_id=candidate_id)
        if self.on_candidate_selected:
            self.on_candidate_selected(candidate_id)
        if self.on_data_changed:
            self.on_data_changed()

    def _delete_candidate(self) -> None:
        if self.current_candidate_id is None:
            QMessageBox.information(
                self,
                _t(self.ui_language, "删除求职者", "Delete Candidate"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return
        answer = QMessageBox.question(
            self,
            _t(self.ui_language, "删除求职者", "Delete Candidate"),
            _t(
                self.ui_language,
                "删除求职者会同时删除这个人的简历、目标岗位和关联状态。确定继续吗？",
                "Deleting this candidate will also remove resume, target roles, and related states. Continue?",
            ),
        )
        if answer != QMessageBox.Yes:
            return

        self.context.candidates.delete(self.current_candidate_id)
        self.current_candidate_id = None
        self.reload()
        if self.on_data_changed:
            self.on_data_changed()
        if self.on_candidate_selected:
            self.on_candidate_selected(None)

    def _open_workspace(self) -> None:
        candidate_id = self.current_candidate_id
        if candidate_id is None:
            candidate_id = self._selected_candidate_id_from_list()
        if candidate_id is None:
            QMessageBox.information(
                self,
                _t(self.ui_language, "进入工作台", "Open Workspace"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return
        self.current_candidate_id = candidate_id
        self._update_action_state()
        if self.on_candidate_selected:
            self.on_candidate_selected(candidate_id)
        if self.on_open_workspace:
            self.on_open_workspace()

    def _on_language_changed(self, _index: int) -> None:
        selected_language = str(self.language_combo.currentData() or "zh")
        normalized = "en" if selected_language == "en" else "zh"
        if normalized == self.ui_language:
            return
        self.context.settings.save_ui_language(normalized)
        if self.on_ui_language_changed:
            self.on_ui_language_changed(normalized)

    def _update_action_state(self) -> None:
        has_selection = self.current_candidate_id is not None
        self.open_workspace_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)


class CandidateBasicsStep(QWidget):
    def __init__(
        self,
        context: AppContext,
        ui_language: str = "zh",
        on_data_changed: Callable[[], None] | None = None,
        on_candidate_saved: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__()
        self.context = context
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.on_data_changed = on_data_changed
        self.on_candidate_saved = on_candidate_saved
        self.current_candidate_id: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(
            make_page_title(
                _t(self.ui_language, "第一步：基本信息", "Step 1: Basics"),
                _t(
                    self.ui_language,
                    "这里维护这个求职者自己的基础信息，比如当前所在地、希望找工作的地点、简历，以及职业背景和专业摘要。",
                    "Maintain this candidate's core profile here, including location, preferred job locations, resume, and the professional background summary used by AI.",
                ),
            )
        )

        card = make_card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(14)

        self.form = CandidateForm(
            save_button_text=_t(self.ui_language, "保存这个求职者的基础信息", "Save Candidate Basics"),
            ui_language=self.ui_language,
        )
        card_layout.addWidget(self.form)
        layout.addWidget(card)
        layout.addStretch(1)

        self.form.save_button.clicked.connect(self._save_candidate)
        self.set_candidate(None)

    def set_candidate(self, candidate_id: int | None) -> None:
        self.current_candidate_id = candidate_id
        if candidate_id is None:
            self.form.clear(_t(self.ui_language, "请先选择一个求职者，再进入工作台。", "Select a candidate first, then open the workspace."))
            self.form.set_form_enabled(False)
            return

        record = self.context.candidates.get(candidate_id)
        if record is None:
            self.form.clear(_t(self.ui_language, "当前求职者不存在，请重新选择。", "Candidate not found. Please select again."))
            self.form.set_form_enabled(False)
            self.current_candidate_id = None
            return

        self.form.load_record(record)
        self.form.set_form_enabled(True)

    def _save_candidate(self) -> None:
        if self.current_candidate_id is None:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "保存失败", "Save Failed"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return
        try:
            candidate_id = self.context.candidates.save(self.form.to_record(self.current_candidate_id))
        except ValueError as exc:
            QMessageBox.warning(self, _t(self.ui_language, "保存失败", "Save Failed"), str(exc))
            return

        self.set_candidate(candidate_id)
        if self.on_data_changed:
            self.on_data_changed()
        if self.on_candidate_saved:
            self.on_candidate_saved(candidate_id)


class StrategyStep(QWidget):
    SCOPE_OPTIONS = [
        ("氢能主线", "hydrogen_mainline"),
        ("副线：MBSE / 系统验证 / 技术接口", "adjacent_mbse"),
    ]

    def __init__(self, context: AppContext, on_data_changed: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.context = context
        self.on_data_changed = on_data_changed
        self.current_candidate_id: int | None = None
        self.current_profile_id: int | None = None
        self.profile_records: list[SearchProfileRecord] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(
            make_page_title(
                "第二步：AI 生成搜索方向",
                "这一步会把基础信息翻译成真正可执行的搜索方向。AI 负责生成岗位方向、公司方向、搜索关键词和公开岗位搜索语句。",
            )
        )

        ai_card = make_card()
        ai_layout = QVBoxLayout(ai_card)
        ai_layout.setContentsMargins(18, 18, 18, 18)
        ai_layout.setSpacing(12)
        ai_title = QLabel("AI 设置")
        ai_title.setObjectName("PageTitle")
        ai_title.setStyleSheet("font-size: 18px;")
        ai_note = QLabel("要使用 AI 生成搜索方向，先在这里填 OpenAI API Key。这个设置会保存在本地。")
        ai_note.setObjectName("MutedLabel")
        ai_note.setWordWrap(True)
        ai_layout.addWidget(ai_title)
        ai_layout.addWidget(ai_note)

        ai_form = QFormLayout()
        ai_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        ai_form.setFormAlignment(Qt.AlignTop)
        ai_form.setHorizontalSpacing(14)
        ai_form.setVerticalSpacing(12)
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("sk-...")
        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText("gpt-5")
        self.save_ai_settings_button = styled_button("保存 AI 设置", "primary")
        ai_form.addRow("OpenAI API Key", self.api_key_input)
        ai_form.addRow("模型", self.model_input)
        ai_form.addRow("", self.save_ai_settings_button)
        ai_layout.addLayout(ai_form)
        layout.addWidget(ai_card)
        ai_card.setVisible(False)

        summary_card = make_card()
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(18, 16, 18, 16)
        summary_layout.setSpacing(12)
        self.strategy_hint_label = QLabel(
            "这里不是手工写脚本的地方，而是把“想找什么工作”翻译成搜索引擎要用的方向。你确认后，系统才会去建公司库、搜公开岗位。"
        )
        self.strategy_hint_label.setWordWrap(True)
        self.strategy_hint_label.setObjectName("MutedLabel")

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)
        scope_label_widget = QLabel("搜索路线")
        scope_label_widget.setObjectName("MutedLabel")
        self.scope_combo = QComboBox()
        for label, value in self.SCOPE_OPTIONS:
            self.scope_combo.addItem(label, value)
        self.generate_strategy_button = styled_button("用 AI 生成方向", "secondary")
        self.save_profile_button = styled_button("保存当前方向", "primary")
        top_row.addWidget(scope_label_widget)
        top_row.addWidget(self.scope_combo)
        top_row.addStretch(1)
        top_row.addWidget(self.generate_strategy_button)
        top_row.addWidget(self.save_profile_button)

        self.profile_meta_label = QLabel("请先选择当前求职者。")
        self.profile_meta_label.setObjectName("MutedLabel")
        self.profile_meta_label.setWordWrap(True)

        summary_layout.addWidget(self.strategy_hint_label)
        summary_layout.addLayout(top_row)
        summary_layout.addWidget(self.profile_meta_label)
        layout.addWidget(summary_card)

        direction_card = make_card()
        direction_layout = QVBoxLayout(direction_card)
        direction_layout.setContentsMargins(18, 18, 18, 18)
        direction_layout.setSpacing(14)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)

        self.target_role_input = QPlainTextEdit()
        self.target_role_input.setPlaceholderText(
            "AI 生成的目标岗位方向，一行一个，例如：\nSystems Engineer\nMBSE Engineer\nVerification & Validation Engineer"
        )
        self.target_role_input.setMinimumHeight(78)

        self.region_priority_input = QPlainTextEdit()
        self.region_priority_input.setPlaceholderText(
            "AI 生成的地区搜索优先级，一行一个，例如：\nSpain / Portugal\nChina\nJapan"
        )
        self.region_priority_input.setMinimumHeight(72)

        self.company_focus_input = QPlainTextEdit()
        self.company_focus_input.setPlaceholderText(
            "AI 生成的目标公司类型，例如：\n工业气体公司\n汽车与复杂装备\n电解槽与系统集成商"
        )
        self.company_focus_input.setMinimumHeight(72)

        self.company_keyword_input = QPlainTextEdit()
        self.company_keyword_input.setPlaceholderText(
            "搜索公司的关键词，一行一个，例如：\nindustrial gas hydrogen company\nPEM electrolyzer OEM\nMBSE systems engineering employer"
        )
        self.company_keyword_input.setMinimumHeight(72)

        self.keyword_focus_input = QPlainTextEdit()
        self.keyword_focus_input.setPlaceholderText(
            "搜索岗位的关键词，一行一个，例如：\nverification and validation engineer\nsystems engineer hydrogen\ndigital twin engineer"
        )
        self.keyword_focus_input.setMinimumHeight(72)

        self.queries_input = QPlainTextEdit()
        self.queries_input.setPlaceholderText(
            "公开岗位搜索语句，一行一个。例如：\nsite:careers.company.com systems engineer job"
        )
        self.queries_input.setMinimumHeight(128)

        form.addRow("目标岗位方向", self.target_role_input)
        form.addRow("地区搜索优先级", self.region_priority_input)
        form.addRow("目标公司类型", self.company_focus_input)
        form.addRow("搜索公司的关键词", self.company_keyword_input)
        form.addRow("搜索岗位的关键词", self.keyword_focus_input)
        form.addRow("公开岗位搜索语句", self.queries_input)
        direction_layout.addLayout(form)
        layout.addWidget(direction_card, 1)

        self.save_ai_settings_button.clicked.connect(self._save_ai_settings)
        self.generate_strategy_button.clicked.connect(self._show_generation_hint)
        self.save_profile_button.clicked.connect(self._save_profile)
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)

        self._load_ai_settings()
        self._set_enabled(False)

    def set_candidate(self, candidate_id: int | None) -> None:
        self.current_candidate_id = candidate_id
        self.current_profile_id = None
        if candidate_id is None:
            self._clear_profile_form()
            self.profile_meta_label.setText("请先选择一个求职者，再进入工作台。")
            self._set_enabled(False)
            return

        candidate = self.context.candidates.get(candidate_id)
        if candidate is None:
            self._clear_profile_form()
            self.profile_meta_label.setText("当前求职者不存在，请重新选择。")
            self._set_enabled(False)
            return

        self.profile_records = self.context.profiles.list_for_candidate(candidate_id)
        self._set_enabled(True)
        self.profile_meta_label.setText(
            f"当前求职者：{candidate.name}。这一步会为这个人生成搜索方向，用于后续收集公司和公开岗位。"
        )
        self._load_current_scope_profile()

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (
            self.save_profile_button,
            self.scope_combo,
            self.target_role_input,
            self.region_priority_input,
            self.company_focus_input,
            self.company_keyword_input,
            self.keyword_focus_input,
            self.queries_input,
        ):
            widget.setEnabled(enabled)

    def _load_ai_settings(self) -> None:
        settings = self.context.settings.get_openai_settings()
        self.api_key_input.setText(settings.api_key)
        self.model_input.setText(settings.model)

    def _save_ai_settings(self) -> None:
        self.context.settings.save_openai_settings(
            OpenAISettings(
                api_key=self.api_key_input.text(),
                model=self.model_input.text(),
            )
        )
        QMessageBox.information(self, "AI 设置", "AI 设置已保存到本地。")

    def _find_profile(self, profile_id: int | None) -> SearchProfileRecord | None:
        for profile in self.profile_records:
            if profile.profile_id == profile_id:
                return profile
        return None

    def _find_profile_for_scope(self, scope_profile: str) -> SearchProfileRecord | None:
        for profile in self.profile_records:
            if profile.scope_profile == scope_profile:
                return profile
        return None

    def _load_profile(self, profile: SearchProfileRecord) -> None:
        self.current_profile_id = profile.profile_id
        scope_index = self.scope_combo.findData(profile.scope_profile)
        self.scope_combo.setCurrentIndex(scope_index if scope_index >= 0 else 0)
        self.target_role_input.setPlainText(profile.target_role)
        self.region_priority_input.setPlainText(profile.location_preference)
        self.company_focus_input.setPlainText(profile.company_focus)
        self.company_keyword_input.setPlainText(profile.company_keyword_focus)
        self.keyword_focus_input.setPlainText(profile.keyword_focus)
        self.queries_input.setPlainText("\n".join(profile.queries))
        self.profile_meta_label.setText(
            f"当前路线：{scope_label(profile.scope_profile)}    ·    最近更新：{profile.updated_at or '-'}"
        )

    def _clear_profile_form(self) -> None:
        self.current_profile_id = None
        self.target_role_input.clear()
        self.region_priority_input.clear()
        self.company_focus_input.clear()
        self.company_keyword_input.clear()
        self.keyword_focus_input.clear()
        self.queries_input.clear()
        if self.current_candidate_id is not None:
            self.profile_meta_label.setText(
                "当前还没有搜索方向。你可以先手动确认这些内容，后面再让 AI 自动生成初稿。"
            )

        if self.current_candidate_id is not None:
            candidate = self.context.candidates.get(self.current_candidate_id)
            if candidate is not None:
                if candidate.target_directions.strip():
                    self.target_role_input.setPlainText(candidate.target_directions)
                if candidate.preferred_locations.strip():
                    self.region_priority_input.setPlainText(candidate.preferred_locations)

    def _load_current_scope_profile(self) -> None:
        scope_profile = str(self.scope_combo.currentData() or "hydrogen_mainline")
        profile = self._find_profile_for_scope(scope_profile)
        if profile is None:
            self._clear_profile_form()
            self.profile_meta_label.setText(
                f"当前路线：{scope_label(scope_profile)}。还没有生成搜索方向，可以先手动补一个初稿。"
            )
            return
        self._load_profile(profile)

    def _on_scope_changed(self) -> None:
        if self.current_candidate_id is None:
            return
        self._load_current_scope_profile()

    def _show_generation_hint(self) -> None:
        if not self.api_key_input.text().strip():
            QMessageBox.warning(self, "AI 搜索方向", "请先填写并保存 OpenAI API Key。")
            return
        QMessageBox.information(
            self,
            "AI 搜索方向",
            "AI 会根据求职者的基础信息生成岗位方向、地区优先级、公司方向、公司关键词、岗位关键词和公开岗位搜索语句。",
        )

    def _save_profile(self) -> None:
        if self.current_candidate_id is None:
            QMessageBox.warning(self, "保存失败", "请先选择一个求职者。")
            return
        scope_profile = str(self.scope_combo.currentData() or "hydrogen_mainline")
        existing_profile = self._find_profile(self.current_profile_id)
        try:
            profile_id = self.context.profiles.save(
                SearchProfileRecord(
                    profile_id=self.current_profile_id,
                    candidate_id=self.current_candidate_id,
                    name=f"{scope_label(scope_profile)} 搜索方向",
                    scope_profile=scope_profile,
                    target_role=self.target_role_input.toPlainText(),
                    location_preference=self.region_priority_input.toPlainText(),
                    company_focus=self.company_focus_input.toPlainText(),
                    company_keyword_focus=self.company_keyword_input.toPlainText(),
                    role_name_i18n=existing_profile.role_name_i18n if existing_profile is not None else "",
                    keyword_focus=self.keyword_focus_input.toPlainText(),
                    is_active=True,
                    queries=self.queries_input.toPlainText().splitlines(),
                )
            )
        except ValueError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return

        self.current_profile_id = profile_id
        self.profile_records = self.context.profiles.list_for_candidate(self.current_candidate_id)
        self._load_current_scope_profile()
        if self.on_data_changed:
            self.on_data_changed()


class CandidatePlaceholderStep(QWidget):
    def __init__(self, title: str, subtitle: str, bullets: list[str]) -> None:
        super().__init__()
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageTitle")
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("PageSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.context_label = QLabel("请选择求职者后继续。")
        self.context_label.setObjectName("MutedLabel")
        self.context_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)

        card = make_card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(10)
        card_layout.addWidget(self.context_label)
        for bullet in bullets:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            dot = QLabel("•")
            text = QLabel(bullet)
            text.setWordWrap(True)
            row_layout.addWidget(dot)
            row_layout.addWidget(text, 1)
            card_layout.addWidget(row)
        layout.addWidget(card)
        layout.addStretch(1)

    def set_candidate(self, candidate: CandidateRecord | None) -> None:
        if candidate is None:
            self.context_label.setText("请先返回选择页，先选择一个求职者。")
            return
        self.context_label.setText(
            f"当前求职者：{candidate.name}。这一步会基于这个人的简历、目标岗位和历史状态继续展开。"
        )


class TargetDirectionStep(QWidget):
    def __init__(
        self,
        context: AppContext,
        ui_language: str = "zh",
        on_data_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.context = context
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.on_data_changed = on_data_changed
        self.role_recommender = OpenAIRoleRecommendationService()
        self.current_candidate_id: int | None = None
        self.current_profile_id: int | None = None
        self.profile_records: list[SearchProfileRecord] = []
        self._auto_translated_profile_ids: set[int] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(
            make_page_title(
                _t(self.ui_language, "第二步：目标岗位设立", "Step 2: Target Roles"),
                _t(
                    self.ui_language,
                    "这里先确定要往哪几个目标岗位投递。后面系统会根据这些岗位去搜索公司和公开岗位。",
                    "Define target roles first. Later search and matching will run based on these roles.",
                ),
            )
        )

        summary_card = make_card()
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(18, 16, 18, 16)
        summary_layout.setSpacing(12)
        self.summary_label = QLabel(
            _t(
                self.ui_language,
                "这里会列出几个目标岗位。每次点一次 AI 推荐，后面只会新增少量岗位，保留下来的岗位会参与后续搜索。",
                "Target roles are listed here. Each AI recommendation adds only a few new roles, and kept roles are used in downstream search.",
            )
        )
        self.summary_label.setWordWrap(True)
        self.summary_label.setObjectName("MutedLabel")
        summary_layout.addWidget(self.summary_label)

        self.profile_meta_label = QLabel(_t(self.ui_language, "请先选择当前求职者。", "Select a candidate first."))
        self.profile_meta_label.setObjectName("MutedLabel")
        self.profile_meta_label.setWordWrap(True)
        summary_layout.addWidget(self.profile_meta_label)
        layout.addWidget(summary_card)

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(16)

        left_card = make_card()
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)
        list_hint = QLabel(
            _t(
                self.ui_language,
                "勾选表示这个岗位会参与后续搜索。",
                "Checked roles will be used in downstream search.",
            )
        )
        list_hint.setObjectName("MutedLabel")
        list_hint.setWordWrap(True)
        left_layout.addWidget(list_hint)

        self.direction_list = QListWidget()
        self.direction_list.setObjectName("TargetRoleList")
        left_layout.addWidget(self.direction_list, 1)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(10)
        self.generate_directions_button = styled_button(_t(self.ui_language, "AI 推荐岗位", "AI Recommend Roles"), "primary")
        self.add_direction_button = styled_button(_t(self.ui_language, "手动添加岗位", "Add Role Manually"), "secondary")
        self.delete_direction_button = styled_button(_t(self.ui_language, "删除岗位", "Delete Role"), "danger")
        action_row.addWidget(self.generate_directions_button)
        action_row.addWidget(self.add_direction_button)
        action_row.addWidget(self.delete_direction_button)
        left_layout.addLayout(action_row)

        right_card = make_card()
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(18, 18, 18, 18)
        right_layout.setSpacing(14)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)

        self.direction_name_input = QLineEdit()
        self.direction_reason_input = QPlainTextEdit()
        self.direction_reason_input.setPlaceholderText(
            _t(
                self.ui_language,
                "这个岗位主要做什么、为什么适合这个求职者。",
                "What this role mainly does, and why it fits this candidate.",
            )
        )
        self.direction_reason_input.setMinimumHeight(150)

        form.addRow(_t(self.ui_language, "岗位名称", "Role Name"), self.direction_name_input)
        form.addRow(_t(self.ui_language, "岗位说明", "Role Description"), self.direction_reason_input)
        right_layout.addLayout(form)
        self.save_direction_button = styled_button(_t(self.ui_language, "保存岗位信息", "Save Role Info"), "primary")
        right_layout.addWidget(self.save_direction_button, 0, Qt.AlignRight)
        right_layout.addStretch(1)

        content_row.addWidget(left_card, 1)
        content_row.addWidget(right_card, 2)
        layout.addLayout(content_row, 1)

        self.generate_directions_button.clicked.connect(self._generate_role_suggestions)
        self.save_direction_button.clicked.connect(self._save_profile)
        self.add_direction_button.clicked.connect(self._add_direction)
        self.delete_direction_button.clicked.connect(self._delete_direction)
        self.direction_list.currentItemChanged.connect(self._on_profile_selected)
        self.direction_list.itemChanged.connect(self._on_item_checked_changed)

        self._set_enabled(False)

    def set_candidate(self, candidate_id: int | None) -> None:
        self.current_candidate_id = candidate_id
        self.current_profile_id = None
        self._auto_translated_profile_ids = set()
        if candidate_id is None:
            self.profile_records = []
            self.direction_list.clear()
            self._clear_profile_form()
            self.profile_meta_label.setText(
                _t(self.ui_language, "请先选择一个求职者，再进入工作台。", "Select a candidate first, then open the workspace.")
            )
            self._set_enabled(False)
            return

        candidate = self.context.candidates.get(candidate_id)
        if candidate is None:
            self.profile_records = []
            self.direction_list.clear()
            self._clear_profile_form()
            self.profile_meta_label.setText(
                _t(self.ui_language, "当前求职者不存在，请重新选择。", "Candidate not found. Please select again.")
            )
            self._set_enabled(False)
            return

        self._set_enabled(True)
        self.profile_meta_label.setText(
            _t(
                self.ui_language,
                f"当前求职者：{candidate.name}。先确定要重点投的目标岗位，后面系统才会按这些岗位去找公司和岗位。",
                f"Current candidate: {candidate.name}. Confirm target roles first; downstream company/job search uses these roles.",
            )
        )
        self._reload_profiles()

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (
            self.generate_directions_button,
            self.save_direction_button,
            self.add_direction_button,
            self.delete_direction_button,
            self.direction_list,
            self.direction_name_input,
            self.direction_reason_input,
        ):
            widget.setEnabled(enabled)

    def _reload_profiles(self, preserve_profile_id: int | None = None) -> None:
        if self.current_candidate_id is None:
            self.profile_records = []
            self.direction_list.clear()
            self._clear_profile_form()
            return

        self.profile_records = sorted(
            self.context.profiles.list_for_candidate(self.current_candidate_id),
            key=lambda profile: (
                self._display_role_name(profile).casefold(),
                profile.profile_id or 0,
            ),
        )
        self.direction_list.blockSignals(True)
        self.direction_list.clear()
        target_row = None
        target_profile_id = preserve_profile_id if preserve_profile_id is not None else self.current_profile_id
        for row_index, raw_profile in enumerate(self.profile_records):
            profile = self._ensure_profile_bilingual_for_ui(raw_profile)
            display_name = self._display_role_name(profile) or _t(self.ui_language, "未命名岗位", "Untitled Role")
            item = QListWidgetItem(display_name)
            item.setData(Qt.UserRole, profile.profile_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if profile.is_active else Qt.Unchecked)
            self.direction_list.addItem(item)
            if target_profile_id == profile.profile_id:
                target_row = row_index
        self.direction_list.blockSignals(False)

        if target_row is not None:
            self.direction_list.setCurrentRow(target_row)
            return
        if self.profile_records:
            self.direction_list.setCurrentRow(0)
            return
        self._clear_profile_form()

    def _find_profile(self, profile_id: int | None) -> SearchProfileRecord | None:
        for profile in self.profile_records:
            if profile.profile_id == profile_id:
                return profile
        return None

    def _display_role_name(self, profile: SearchProfileRecord) -> str:
        return select_bilingual_role_name(
            profile.role_name_i18n,
            self.ui_language,
            fallback_name=profile.name,
        )

    @staticmethod
    def _canonical_role_name(role_name_i18n: str, fallback_name: str = "") -> str:
        name_zh, name_en = decode_bilingual_role_name(role_name_i18n, fallback_name=fallback_name)
        return name_en or name_zh or str(fallback_name or "").strip()

    def _complete_role_name_pair(
        self,
        name_zh: str,
        name_en: str,
        settings: OpenAISettings,
        api_base_url: str,
        use_ai: bool,
    ) -> tuple[str, str]:
        completed_zh = str(name_zh or "").strip()
        completed_en = str(name_en or "").strip()
        if completed_zh and completed_en:
            return completed_zh, completed_en

        if use_ai:
            try:
                if not completed_zh and completed_en:
                    completed_zh = self.role_recommender.translate_role_name(
                        role_name=completed_en,
                        target_language="zh",
                        settings=settings,
                        api_base_url=api_base_url,
                    ).strip()
                elif not completed_en and completed_zh:
                    completed_en = self.role_recommender.translate_role_name(
                        role_name=completed_zh,
                        target_language="en",
                        settings=settings,
                        api_base_url=api_base_url,
                    ).strip()
            except RoleRecommendationError:
                pass

        if not completed_zh and completed_en:
            completed_zh = completed_en
        if not completed_en and completed_zh:
            completed_en = completed_zh
        return completed_zh, completed_en

    def _complete_description_pair(
        self,
        role_name: str,
        description_zh: str,
        description_en: str,
        settings: OpenAISettings,
        api_base_url: str,
        use_ai: bool,
    ) -> tuple[str, str]:
        completed_zh = str(description_zh or "").strip()
        completed_en = str(description_en or "").strip()
        if not completed_zh and not completed_en:
            return "", ""
        if completed_zh and completed_en:
            return completed_zh, completed_en

        if use_ai:
            try:
                if not completed_en and completed_zh:
                    completed_en = self.role_recommender.translate_description_to_english(
                        role_name=role_name,
                        description_zh=completed_zh,
                        settings=settings,
                        api_base_url=api_base_url,
                    ).strip()
                elif not completed_zh and completed_en:
                    completed_zh = self.role_recommender.translate_description_to_chinese(
                        role_name=role_name,
                        description_en=completed_en,
                        settings=settings,
                        api_base_url=api_base_url,
                    ).strip()
            except RoleRecommendationError:
                pass

        if not completed_zh and completed_en:
            completed_zh = completed_en
        if not completed_en and completed_zh:
            completed_en = completed_zh
        return completed_zh, completed_en

    def _load_profile(self, profile: SearchProfileRecord) -> None:
        profile = self._ensure_profile_bilingual_for_ui(profile)
        self.current_profile_id = profile.profile_id
        display_name = self._display_role_name(profile) or _t(self.ui_language, "未命名岗位", "Untitled Role")
        self.direction_name_input.setText(display_name)
        self.direction_reason_input.setPlainText(
            select_bilingual_description(profile.keyword_focus, self.ui_language)
        )
        self.profile_meta_label.setText(
            _t(
                self.ui_language,
                f"当前岗位：{display_name}",
                f"Current role: {display_name}",
            )
        )

    def _ensure_profile_bilingual_for_ui(self, profile: SearchProfileRecord) -> SearchProfileRecord:
        if profile.profile_id is None:
            return profile

        current_name = str(profile.name or "").strip()
        name_zh, name_en = decode_bilingual_role_name(
            profile.role_name_i18n,
            fallback_name=current_name,
        )
        description_zh, description_en = decode_bilingual_description(profile.keyword_focus)

        needs_role_name_completion = not (name_zh.strip() and name_en.strip())
        has_any_description = bool(description_zh.strip() or description_en.strip())
        needs_description_completion = has_any_description and not (
            description_zh.strip() and description_en.strip()
        )
        if not needs_role_name_completion and not needs_description_completion:
            return profile

        settings = self.context.settings.get_effective_openai_settings()
        api_base_url = self.context.settings.get_openai_base_url()
        # Do not trigger AI calls while list is loading; keep UI responsive.
        completed_name_zh, completed_name_en = self._complete_role_name_pair(
            name_zh=name_zh,
            name_en=name_en,
            settings=settings,
            api_base_url=api_base_url,
            use_ai=False,
        )
        canonical_name = completed_name_en or completed_name_zh or current_name
        completed_description_zh, completed_description_en = self._complete_description_pair(
            role_name=canonical_name,
            description_zh=description_zh,
            description_en=description_en,
            settings=settings,
            api_base_url=api_base_url,
            use_ai=False,
        )

        updated_role_name_i18n = encode_bilingual_role_name(completed_name_zh, completed_name_en)
        updated_name = self._canonical_role_name(updated_role_name_i18n, fallback_name=current_name)
        if not updated_name:
            updated_name = _t(self.ui_language, "未命名岗位", "Untitled Role")
        updated_target_role = updated_name
        if completed_description_zh or completed_description_en:
            updated_keyword_focus = encode_bilingual_description(
                completed_description_zh,
                completed_description_en,
            )
        else:
            updated_keyword_focus = str(profile.keyword_focus or "").strip()

        current_role_name_i18n = str(profile.role_name_i18n or "").strip()
        current_keyword_focus = str(profile.keyword_focus or "").strip()
        current_target_role = str(profile.target_role or "").strip()
        if (
            updated_role_name_i18n == current_role_name_i18n
            and updated_keyword_focus == current_keyword_focus
            and updated_name == current_name
            and updated_target_role == current_target_role
        ):
            return profile

        updated_profile_id = self.context.profiles.save(
            SearchProfileRecord(
                profile_id=profile.profile_id,
                candidate_id=profile.candidate_id,
                name=updated_name,
                scope_profile=profile.scope_profile,
                target_role=updated_target_role,
                location_preference=profile.location_preference,
                company_focus=profile.company_focus,
                company_keyword_focus=profile.company_keyword_focus,
                role_name_i18n=updated_role_name_i18n,
                keyword_focus=updated_keyword_focus,
                is_active=profile.is_active,
                queries=profile.queries,
            )
        )
        refreshed = self.context.profiles.get(updated_profile_id)
        if refreshed is None:
            refreshed = SearchProfileRecord(
                profile_id=profile.profile_id,
                candidate_id=profile.candidate_id,
                name=updated_name,
                scope_profile=profile.scope_profile,
                target_role=updated_target_role,
                location_preference=profile.location_preference,
                company_focus=profile.company_focus,
                company_keyword_focus=profile.company_keyword_focus,
                role_name_i18n=updated_role_name_i18n,
                keyword_focus=updated_keyword_focus,
                is_active=profile.is_active,
                queries=profile.queries,
                created_at=profile.created_at,
                updated_at=profile.updated_at,
            )

        for index, record in enumerate(self.profile_records):
            if record.profile_id == refreshed.profile_id:
                self.profile_records[index] = refreshed
                break
        return refreshed

    def _clear_profile_form(self) -> None:
        self.current_profile_id = None
        self.direction_name_input.clear()
        self.direction_reason_input.clear()
        if self.current_candidate_id is not None:
            self.profile_meta_label.setText(
                _t(
                    self.ui_language,
                    "当前还没有目标岗位。你可以点击“AI 推荐岗位”，或者手动添加一个岗位。",
                    "No target role yet. Click AI recommendation or add one manually.",
                )
            )

    def _on_profile_selected(self, current: QListWidgetItem | None, _: QListWidgetItem | None) -> None:
        if current is None:
            self._clear_profile_form()
            return
        profile_id = current.data(Qt.UserRole)
        self.current_profile_id = int(profile_id) if profile_id is not None else None
        profile = self._find_profile(self.current_profile_id)
        if profile is None:
            self._clear_profile_form()
            return
        self._load_profile(profile)

    def _on_item_checked_changed(self, item: QListWidgetItem) -> None:
        profile_id = item.data(Qt.UserRole)
        if profile_id is None:
            return
        profile = self._find_profile(int(profile_id))
        if profile is None:
            return
        self.context.profiles.save(
            SearchProfileRecord(
                profile_id=profile.profile_id,
                candidate_id=profile.candidate_id,
                name=profile.name,
                scope_profile=profile.scope_profile,
                target_role=profile.target_role,
                location_preference=profile.location_preference,
                company_focus=profile.company_focus,
                company_keyword_focus=profile.company_keyword_focus,
                role_name_i18n=profile.role_name_i18n,
                keyword_focus=profile.keyword_focus,
                is_active=item.checkState() == Qt.Checked,
                queries=profile.queries,
            )
        )
        self.profile_records = sorted(
            self.context.profiles.list_for_candidate(self.current_candidate_id or 0),
            key=lambda current: (self._display_role_name(current).casefold(), current.profile_id or 0),
        )
        if self.on_data_changed:
            self.on_data_changed()

    def _generate_role_suggestions(self) -> None:
        if self.current_candidate_id is None:
            QMessageBox.information(
                self,
                _t(self.ui_language, "AI 推荐岗位", "AI Recommend Roles"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return
        candidate = self.context.candidates.get(self.current_candidate_id)
        if candidate is None:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "AI 推荐岗位", "AI Recommend Roles"),
                _t(self.ui_language, "当前求职者不存在，请重新选择。", "Candidate not found. Please select again."),
            )
            return
        settings = self.context.settings.get_effective_openai_settings()
        if not settings.api_key.strip():
            QMessageBox.warning(
                self,
                _t(self.ui_language, "AI 推荐岗位", "AI Recommend Roles"),
                _t(
                    self.ui_language,
                    "请先在右上角“AI 设置”里填写并保存 OpenAI API Key。",
                    "Please fill and save OpenAI API Key in the top-right AI settings first.",
                ),
            )
            return

        candidate_id = int(self.current_candidate_id)
        existing_profiles = self.context.profiles.list_for_candidate(candidate_id)
        existing_role_context = [
            (
                self._canonical_role_name(profile.role_name_i18n, fallback_name=profile.name),
                description_for_prompt(profile.keyword_focus),
            )
            for profile in existing_profiles
            if self._canonical_role_name(profile.role_name_i18n, fallback_name=profile.name)
        ]

        self.generate_directions_button.setEnabled(False)
        dialog_title = _t(self.ui_language, "AI 推荐岗位", "AI Recommend Roles")
        dialog_message = _t(
            self.ui_language,
            "AI 正在生成岗位推荐，请稍候...",
            "AI is generating role recommendations, please wait...",
        )

        def _task() -> Any:
            return self.role_recommender.recommend_roles(
                candidate,
                settings,
                api_base_url=self.context.settings.get_openai_base_url(),
                max_items=2 if existing_role_context else 3,
                existing_roles=existing_role_context,
            )

        def _on_success(result: Any) -> None:
            if self.current_candidate_id != candidate_id:
                return
            suggestions = list(result) if isinstance(result, list) else []
            existing_name_keys: set[str] = set()
            for profile in self.context.profiles.list_for_candidate(candidate_id):
                for name_line in role_name_query_lines(profile.role_name_i18n, fallback_name=profile.name):
                    existing_name_keys.add(name_line.casefold())
            added_names: list[str] = []
            last_profile_id: int | None = None
            for suggestion in suggestions:
                suggestion_name_i18n = encode_bilingual_role_name(
                    suggestion.name_zh,
                    suggestion.name_en or suggestion.name,
                )
                suggestion_keys = {
                    line.casefold()
                    for line in role_name_query_lines(
                        suggestion_name_i18n,
                        fallback_name=suggestion.name,
                    )
                }
                if suggestion_keys & existing_name_keys:
                    continue
                canonical_name = self._canonical_role_name(
                    suggestion_name_i18n,
                    fallback_name=suggestion.name,
                )
                last_profile_id = self.context.profiles.save(
                    SearchProfileRecord(
                        profile_id=None,
                        candidate_id=candidate_id,
                        name=canonical_name,
                        scope_profile=suggestion.scope_profile,
                        target_role=canonical_name,
                        location_preference=candidate.preferred_locations,
                        company_focus="",
                        company_keyword_focus="",
                        role_name_i18n=suggestion_name_i18n,
                        keyword_focus=encode_bilingual_description(
                            suggestion.description_zh,
                            suggestion.description_en,
                        ),
                        is_active=True,
                        queries=[],
                    )
                )
                existing_name_keys.update(suggestion_keys)
                added_names.append(
                    select_bilingual_role_name(
                        suggestion_name_i18n,
                        self.ui_language,
                        fallback_name=canonical_name,
                    )
                )

            self._reload_profiles(preserve_profile_id=last_profile_id)
            if self.on_data_changed:
                self.on_data_changed()

            if added_names:
                QMessageBox.information(
                    self,
                    dialog_title,
                    _t(self.ui_language, "这次已新增这些岗位：\n- ", "Added these roles this time:\n- ")
                    + "\n- ".join(added_names),
                )
                return

            QMessageBox.information(
                self,
                dialog_title,
                _t(
                    self.ui_language,
                    "这次返回的岗位和现有列表重复，没有新增内容。你可以再点一次，或者手动补一个岗位。",
                    "Returned roles duplicate existing ones, so nothing new was added. Try again or add one manually.",
                ),
            )

        def _on_error(exc: Exception) -> None:
            if isinstance(exc, RoleRecommendationError):
                QMessageBox.warning(self, dialog_title, str(exc))
                return
            QMessageBox.warning(
                self,
                dialog_title,
                _t(
                    self.ui_language,
                    f"AI 推荐失败：{exc}",
                    f"AI recommendation failed: {exc}",
                ),
            )

        def _on_finally() -> None:
            self.generate_directions_button.setEnabled(self.current_candidate_id is not None)

        started = run_busy_task(
            self,
            title=dialog_title,
            message=dialog_message,
            task=_task,
            on_success=_on_success,
            on_error=_on_error,
            on_finally=_on_finally,
        )
        if not started:
            self.generate_directions_button.setEnabled(self.current_candidate_id is not None)

    def _add_direction(self) -> None:
        if self.current_candidate_id is None:
            QMessageBox.information(
                self,
                _t(self.ui_language, "手动添加岗位", "Add Role Manually"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return
        candidate = self.context.candidates.get(self.current_candidate_id)
        if candidate is None:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "手动添加岗位", "Add Role Manually"),
                _t(self.ui_language, "当前求职者不存在，请重新选择。", "Candidate not found. Please select again."),
            )
            return
        dialog = ManualRoleInputDialog(ui_language=self.ui_language, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        direction_name, rough_description = dialog.values()
        if not direction_name:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "手动添加岗位", "Add Role Manually"),
                _t(self.ui_language, "岗位名称不能为空。", "Role name cannot be empty."),
            )
            return

        candidate_id = int(self.current_candidate_id)
        settings = self.context.settings.get_effective_openai_settings()
        api_base_url = self.context.settings.get_openai_base_url()
        use_ai = bool(settings.api_key.strip())
        dialog_title = _t(self.ui_language, "手动添加岗位", "Add Role Manually")

        def _build_payload(enable_ai: bool) -> dict[str, str]:
            suggestion = None
            enrich_error = ""
            if enable_ai:
                try:
                    suggestion = self.role_recommender.enrich_manual_role(
                        candidate=candidate,
                        settings=settings,
                        role_name=direction_name,
                        rough_description=rough_description,
                        api_base_url=api_base_url,
                    )
                except RoleRecommendationError as exc:
                    enrich_error = str(exc)

            if suggestion is not None:
                role_name_zh = suggestion.name_zh.strip()
                role_name_en = (suggestion.name_en or suggestion.name).strip()
                description_zh = suggestion.description_zh.strip()
                description_en = suggestion.description_en.strip()
                scope_profile = suggestion.scope_profile or "hydrogen_mainline"
            else:
                role_name_zh = direction_name if self.ui_language != "en" else ""
                role_name_en = direction_name if self.ui_language == "en" else ""
                description_zh = rough_description if self.ui_language != "en" else ""
                description_en = rough_description if self.ui_language == "en" else ""
                scope_profile = "hydrogen_mainline"

            role_name_zh, role_name_en = self._complete_role_name_pair(
                name_zh=role_name_zh,
                name_en=role_name_en,
                settings=settings,
                api_base_url=api_base_url,
                use_ai=enable_ai,
            )
            canonical_name_for_translate = role_name_en or role_name_zh or direction_name
            description_zh, description_en = self._complete_description_pair(
                role_name=canonical_name_for_translate,
                description_zh=description_zh,
                description_en=description_en,
                settings=settings,
                api_base_url=api_base_url,
                use_ai=enable_ai,
            )
            return {
                "role_name_zh": role_name_zh,
                "role_name_en": role_name_en,
                "description_zh": description_zh,
                "description_en": description_en,
                "scope_profile": scope_profile,
                "enrich_error": enrich_error,
            }

        def _persist_payload(payload: dict[str, str]) -> None:
            role_name_zh = str(payload.get("role_name_zh") or "").strip()
            role_name_en = str(payload.get("role_name_en") or "").strip()
            description_zh = str(payload.get("description_zh") or "").strip()
            description_en = str(payload.get("description_en") or "").strip()
            scope_profile = str(payload.get("scope_profile") or "").strip() or "hydrogen_mainline"
            enrich_error = str(payload.get("enrich_error") or "").strip()

            role_name_i18n = encode_bilingual_role_name(role_name_zh, role_name_en)
            canonical_name = self._canonical_role_name(role_name_i18n, fallback_name=direction_name)
            if is_generic_role_name(canonical_name):
                QMessageBox.warning(
                    self,
                    dialog_title,
                    _t(
                        self.ui_language,
                        "岗位名称还是过于泛化（例如仅 Engineer/Manager）。请补充更具体方向后再提交。",
                        "Role name is still too generic (for example Engineer/Manager only). "
                        "Please add a more specific direction and submit again.",
                    ),
                )
                return
            keyword_focus = (
                encode_bilingual_description(description_zh, description_en)
                if (description_zh or description_en)
                else ""
            )
            profile_id = self.context.profiles.save(
                SearchProfileRecord(
                    profile_id=None,
                    candidate_id=candidate_id,
                    name=canonical_name,
                    scope_profile=scope_profile,
                    target_role=canonical_name,
                    location_preference=candidate.preferred_locations,
                    company_focus="",
                    company_keyword_focus="",
                    role_name_i18n=role_name_i18n,
                    keyword_focus=keyword_focus,
                    is_active=True,
                    queries=[],
                )
            )
            self._reload_profiles(preserve_profile_id=profile_id)
            if self.on_data_changed:
                self.on_data_changed()

            if enrich_error:
                QMessageBox.information(
                    self,
                    dialog_title,
                    _t(
                        self.ui_language,
                        "AI 自动补全失败，已先按输入创建岗位。你可以继续编辑该岗位说明。\n\n"
                        f"错误信息：{enrich_error}",
                        "AI enrichment failed, so the role was created from your input first. "
                        "You can keep editing the role details.\n\n"
                        f"Error: {enrich_error}",
                    ),
                )

        if not use_ai:
            _persist_payload(_build_payload(False))
            return

        self.add_direction_button.setEnabled(False)
        busy_message = _t(
            self.ui_language,
            "AI 正在补全岗位信息，请稍候...",
            "AI is enriching role details, please wait...",
        )

        def _task() -> Any:
            return _build_payload(True)

        def _on_success(result: Any) -> None:
            if self.current_candidate_id != candidate_id:
                return
            if not isinstance(result, dict):
                QMessageBox.warning(
                    self,
                    dialog_title,
                    _t(self.ui_language, "返回结果格式异常。", "Unexpected result payload."),
                )
                return
            _persist_payload(result)

        def _on_error(exc: Exception) -> None:
            QMessageBox.warning(
                self,
                dialog_title,
                _t(
                    self.ui_language,
                    f"岗位补全失败：{exc}",
                    f"Role enrichment failed: {exc}",
                ),
            )

        def _on_finally() -> None:
            self.add_direction_button.setEnabled(self.current_candidate_id is not None)

        started = run_busy_task(
            self,
            title=dialog_title,
            message=busy_message,
            task=_task,
            on_success=_on_success,
            on_error=_on_error,
            on_finally=_on_finally,
        )
        if not started:
            self.add_direction_button.setEnabled(self.current_candidate_id is not None)

    def _save_profile(self) -> None:
        if self.current_candidate_id is None:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "保存失败", "Save Failed"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return
        if self.current_profile_id is None:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "保存失败", "Save Failed"),
                _t(self.ui_language, "请先选择或创建一个岗位。", "Please select or create a role first."),
            )
            return
        current_item = self.direction_list.currentItem()
        is_active = current_item.checkState() == Qt.Checked if current_item is not None else True
        candidate = self.context.candidates.get(self.current_candidate_id)
        preferred_locations = candidate.preferred_locations if candidate is not None else ""
        existing_profile = self._find_profile(self.current_profile_id)
        existing_role_name_zh, existing_role_name_en = decode_bilingual_role_name(
            existing_profile.role_name_i18n if existing_profile is not None else "",
            fallback_name=existing_profile.name if existing_profile is not None else "",
        )
        existing_zh, existing_en = decode_bilingual_description(
            existing_profile.keyword_focus if existing_profile is not None else ""
        )
        edited_name = self.direction_name_input.text().strip() or _t(self.ui_language, "未命名岗位", "Untitled Role")
        edited_description = self.direction_reason_input.toPlainText().strip()
        if self.ui_language == "en":
            role_name_zh = existing_role_name_zh
            role_name_en = edited_name
            description_zh = existing_zh
            description_en = edited_description
        else:
            role_name_zh = edited_name
            role_name_en = existing_role_name_en
            description_zh = edited_description
            description_en = existing_en

        settings = self.context.settings.get_effective_openai_settings()
        api_base_url = self.context.settings.get_openai_base_url()
        use_ai = bool(settings.api_key.strip())
        candidate_id = int(self.current_candidate_id)
        profile_id_for_save = int(self.current_profile_id)
        dialog_title = _t(self.ui_language, "保存失败", "Save Failed")

        def _persist(
            translated_role_name_zh: str,
            translated_role_name_en: str,
            translated_description_zh: str,
            translated_description_en: str,
        ) -> None:
            role_name_i18n = encode_bilingual_role_name(translated_role_name_zh, translated_role_name_en)
            canonical_name = self._canonical_role_name(role_name_i18n, fallback_name=edited_name)
            if not canonical_name:
                canonical_name = _t(self.ui_language, "未命名岗位", "Untitled Role")
            if is_generic_role_name(canonical_name):
                QMessageBox.warning(
                    self,
                    dialog_title,
                    _t(
                        self.ui_language,
                        "岗位名称过于泛化（例如仅 Engineer/Manager）。请改成更具体的岗位方向后再保存。",
                        "Role name is too generic (for example Engineer/Manager only). "
                        "Please make it more specific before saving.",
                    ),
                )
                return
            keyword_focus = (
                encode_bilingual_description(translated_description_zh, translated_description_en)
                if (translated_description_zh or translated_description_en)
                else ""
            )
            try:
                saved_profile_id = self.context.profiles.save(
                    SearchProfileRecord(
                        profile_id=profile_id_for_save,
                        candidate_id=candidate_id,
                        name=canonical_name,
                        scope_profile=(existing_profile.scope_profile if existing_profile is not None else "hydrogen_mainline"),
                        target_role=canonical_name,
                        location_preference=(
                            existing_profile.location_preference
                            if existing_profile is not None and existing_profile.location_preference.strip()
                            else preferred_locations
                        ),
                        company_focus=existing_profile.company_focus if existing_profile is not None else "",
                        company_keyword_focus=existing_profile.company_keyword_focus if existing_profile is not None else "",
                        role_name_i18n=role_name_i18n,
                        keyword_focus=keyword_focus,
                        is_active=is_active,
                        queries=existing_profile.queries if existing_profile is not None else [],
                    )
                )
            except ValueError as exc:
                QMessageBox.warning(self, dialog_title, str(exc))
                return
            self.current_profile_id = saved_profile_id
            self._reload_profiles(preserve_profile_id=saved_profile_id)
            if self.on_data_changed:
                self.on_data_changed()

        if not use_ai:
            _persist(role_name_zh, role_name_en, description_zh, description_en)
            return

        self.save_direction_button.setEnabled(False)
        busy_message = _t(
            self.ui_language,
            "AI 正在补全双语岗位信息，请稍候...",
            "AI is completing bilingual role details, please wait...",
        )

        def _task() -> Any:
            translated_name_zh, translated_name_en = self._complete_role_name_pair(
                name_zh=role_name_zh,
                name_en=role_name_en,
                settings=settings,
                api_base_url=api_base_url,
                use_ai=True,
            )
            canonical_name_for_translate = translated_name_en or translated_name_zh or edited_name
            translated_desc_zh, translated_desc_en = self._complete_description_pair(
                role_name=canonical_name_for_translate,
                description_zh=description_zh,
                description_en=description_en,
                settings=settings,
                api_base_url=api_base_url,
                use_ai=True,
            )
            return {
                "role_name_zh": translated_name_zh,
                "role_name_en": translated_name_en,
                "description_zh": translated_desc_zh,
                "description_en": translated_desc_en,
            }

        def _on_success(result: Any) -> None:
            if self.current_candidate_id != candidate_id or self.current_profile_id != profile_id_for_save:
                return
            if not isinstance(result, dict):
                QMessageBox.warning(
                    self,
                    dialog_title,
                    _t(self.ui_language, "返回结果格式异常。", "Unexpected result payload."),
                )
                return
            _persist(
                str(result.get("role_name_zh") or ""),
                str(result.get("role_name_en") or ""),
                str(result.get("description_zh") or ""),
                str(result.get("description_en") or ""),
            )

        def _on_error(exc: Exception) -> None:
            QMessageBox.warning(
                self,
                dialog_title,
                _t(
                    self.ui_language,
                    f"AI 补全失败：{exc}",
                    f"AI completion failed: {exc}",
                ),
            )

        def _on_finally() -> None:
            self.save_direction_button.setEnabled(self.current_candidate_id is not None)

        started = run_busy_task(
            self,
            title=_t(self.ui_language, "保存岗位信息", "Save Role Info"),
            message=busy_message,
            task=_task,
            on_success=_on_success,
            on_error=_on_error,
            on_finally=_on_finally,
        )
        if not started:
            self.save_direction_button.setEnabled(self.current_candidate_id is not None)

    def _delete_direction(self) -> None:
        if self.current_profile_id is None:
            QMessageBox.information(
                self,
                _t(self.ui_language, "删除岗位", "Delete Role"),
                _t(self.ui_language, "请先选择一个岗位。", "Please select a role first."),
            )
            return
        answer = QMessageBox.question(
            self,
            _t(self.ui_language, "删除岗位", "Delete Role"),
            _t(self.ui_language, "确定删除当前岗位吗？", "Delete current role?"),
        )
        if answer != QMessageBox.Yes:
            return
        self.context.profiles.delete(self.current_profile_id)
        self.current_profile_id = None
        self._reload_profiles()
        if self.on_data_changed:
            self.on_data_changed()


class SearchResultsStep(QWidget):
    STATUS_CODES = ("pending", "focus", "applied", "offered", "rejected", "dropped")

    def __init__(self, context: AppContext, ui_language: str = "zh") -> None:
        super().__init__()
        self.context = context
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.runner = LegacyJobflowRunner(context.paths.project_root)
        self.current_candidate_id: int | None = None
        self.current_candidate_name = ""
        self.target_role_candidates: list[str] = []
        self.status_by_job_key: dict[str, str] = {}
        self.hidden_job_keys: set[str] = set()
        self._live_results_timer = QTimer(self)
        self._live_results_timer.setInterval(2500)
        self._live_results_timer.timeout.connect(self._refresh_live_results)
        self._live_results_candidate_id: int | None = None
        self._live_results_last_count = -1
        self._live_results_signature: tuple[tuple[object, ...], ...] = ()
        self._search_cancel_event: threading.Event | None = None
        self._notification_toast: QFrame | None = None
        self._notification_timer = QTimer(self)
        self._notification_timer.setSingleShot(True)
        self._notification_timer.timeout.connect(self._hide_notification_toast)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(
            make_page_title(
                _t(self.ui_language, "第三步：岗位搜索结果", "Step 3: Search Results"),
                _t(
                    self.ui_language,
                    "点击“寻找更多岗位”后，系统会继续抓取并分析岗位结果。表格支持评分查看、状态维护和删除。",
                    "Click 'Find More Jobs' to continue discovery and analysis. The table supports score review, status updates, and deletion.",
                ),
            )
        )

        control_card = make_card()
        control_layout = QVBoxLayout(control_card)
        control_layout.setContentsMargins(18, 16, 18, 16)
        control_layout.setSpacing(12)
        self.results_meta_label = QLabel(
            _t(
                self.ui_language,
                "请先选择一个求职者，并确认前面的目标岗位方向。",
                "Select a candidate and confirm target roles first.",
            )
        )
        self.results_meta_label.setObjectName("MutedLabel")
        self.results_meta_label.setWordWrap(True)
        control_layout.addWidget(self.results_meta_label)

        self.results_stats_label = QLabel(
            _t(
                self.ui_language,
                "内部统计：未选择求职者。",
                "Internal stats: no candidate selected.",
            )
        )
        self.results_stats_label.setObjectName("MutedLabel")
        self.results_stats_label.setWordWrap(True)
        control_layout.addWidget(self.results_stats_label)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(10)
        self.refresh_button = styled_button(_t(self.ui_language, "寻找更多岗位", "Find More Jobs"), "primary")
        self.stop_button = styled_button(_t(self.ui_language, "停止搜索", "Stop Search"), "secondary")
        self.delete_button = styled_button(_t(self.ui_language, "删除所选岗位", "Delete Selected Jobs"), "danger")
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.stop_button)
        button_row.addWidget(self.delete_button)
        button_row.addStretch(1)
        control_layout.addLayout(button_row)
        layout.addWidget(control_card)

        table_card = make_card()
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(14, 14, 14, 14)
        table_layout.setSpacing(10)
        self.table = QTableWidget(0, 8)
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
        table_layout.addWidget(self.table)
        layout.addWidget(table_card, 1)

        self.refresh_button.clicked.connect(self._run_search)
        self.stop_button.clicked.connect(self._stop_search)
        self.delete_button.clicked.connect(self._delete_selected_rows)

    def set_candidate(self, candidate: CandidateRecord | None) -> None:
        self._stop_live_results_updates()
        self.table.setRowCount(0)
        self._live_results_signature = ()
        if candidate is None or candidate.candidate_id is None:
            self.current_candidate_id = None
            self.current_candidate_name = ""
            self.target_role_candidates = []
            self.status_by_job_key = {}
            self.hidden_job_keys = set()
            self.results_meta_label.setText(
                _t(
                    self.ui_language,
                    "请先返回选择页，选择一个求职者后再开始搜索。",
                    "Go back to candidate selection and choose a candidate first.",
                )
            )
            self.refresh_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            self._search_cancel_event = None
            self._refresh_results_stats_label()
            return

        self.current_candidate_id = candidate.candidate_id
        self.current_candidate_name = candidate.name
        profiles = self.context.profiles.list_for_candidate(candidate.candidate_id)
        self.target_role_candidates = self._build_target_role_candidates(profiles)
        self._load_review_state(candidate.candidate_id)
        self.refresh_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.delete_button.setEnabled(True)
        self.results_meta_label.setText(
            _t(
                self.ui_language,
                f"当前求职者：{candidate.name}。点击“寻找更多岗位”会继续抓取，并按最新时间排序显示。",
                f"Current candidate: {candidate.name}. Click 'Find More Jobs' to continue discovery, with newest results shown first.",
            )
        )
        self._refresh_results_stats_label()
        self._reload_existing_results(candidate.candidate_id)

    def _reload_existing_results(self, candidate_id: int) -> None:
        jobs = self.runner.load_recommended_jobs(candidate_id)
        visible_count = self._render_jobs(jobs)
        pending_count = self._main_pending_analysis_count(candidate_id)
        if visible_count > 0:
            self.results_meta_label.setText(
                _t(
                    self.ui_language,
                    (
                        f"当前求职者：{self.current_candidate_name}。已加载最近一次运行结果，共 {visible_count} 条；"
                        f"另有 {pending_count} 条上次未补完，只有再次点击“寻找更多岗位”时才会继续处理。"
                        if pending_count > 0
                        else f"当前求职者：{self.current_candidate_name}。已加载最近一次运行结果，共 {visible_count} 条。"
                    ),
                    (
                        f"Current candidate: {self.current_candidate_name}. Loaded latest run results: {visible_count} item(s); "
                        f"{pending_count} unfinished main-stage job(s) from the last run will resume only after you click 'Find More Jobs' again."
                        if pending_count > 0
                        else f"Current candidate: {self.current_candidate_name}. Loaded latest run results: {visible_count} item(s)."
                    ),
                )
            )
        elif pending_count > 0:
            self.results_meta_label.setText(
                _t(
                    self.ui_language,
                    f"当前求职者：{self.current_candidate_name}。当前还没有可展示结果；另有 {pending_count} 条上次未补完，只有再次点击“寻找更多岗位”时才会继续处理。",
                    f"Current candidate: {self.current_candidate_name}. There are no displayable results yet; {pending_count} unfinished main-stage job(s) from the last run will resume only after you click 'Find More Jobs' again.",
                )
            )
        self._refresh_results_stats_label()
        self._live_results_last_count = visible_count

    def _stop_search(self) -> None:
        cancel_event = self._search_cancel_event
        running_thread = getattr(self, "_busy_task_thread", None)
        if cancel_event is None or not isinstance(running_thread, QThread) or not running_thread.isRunning():
            self.stop_button.setEnabled(False)
            return
        cancel_event.set()
        self.stop_button.setEnabled(False)
        self.results_meta_label.setText(
            _t(
                self.ui_language,
                f"当前求职者：{self.current_candidate_name}。已请求停止后台搜索，正在等待当前子阶段安全结束。",
                f"Current candidate: {self.current_candidate_name}. Stop requested. Waiting for the current background stage to end safely.",
            )
        )

    def _run_search(self) -> None:
        if self.current_candidate_id is None:
            QMessageBox.information(
                self,
                _t(self.ui_language, "岗位搜索结果", "Search Results"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return
        candidate = self.context.candidates.get(self.current_candidate_id)
        if candidate is None:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "岗位搜索结果", "Search Results"),
                _t(self.ui_language, "当前求职者不存在，请重新选择。", "Candidate not found. Please select again."),
            )
            return

        profiles = self.context.profiles.list_for_candidate(self.current_candidate_id)
        active_profiles = [profile for profile in profiles if profile.is_active]
        if not active_profiles:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "岗位搜索结果", "Search Results"),
                _t(
                    self.ui_language,
                    "请先在第二步至少勾选一个目标岗位。",
                    "Please check at least one target role in Step 2 first.",
                ),
            )
            return

        candidate_id = int(self.current_candidate_id)
        pending_before_run = self._main_pending_analysis_count(candidate_id)
        self._search_cancel_event = threading.Event()
        self.refresh_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._live_results_last_count = -1
        self.results_meta_label.setText(
            _t(
                self.ui_language,
                (
                    f"当前求职者：{candidate.name}。正在搜索岗位；会先补完上次未完成的 {pending_before_run} 条主流程岗位，再继续寻找新的岗位。"
                    if pending_before_run > 0
                    else f"当前求职者：{candidate.name}。正在后台搜索岗位；只有找到新岗位或结果发生变化时才会刷新列表。你可以继续操作，搜索会在后台持续运行。"
                ),
                (
                    f"Current candidate: {candidate.name}. Searching jobs; {pending_before_run} unfinished main-stage job(s) from the last run will be completed first, then discovery continues."
                    if pending_before_run > 0
                    else f"Current candidate: {candidate.name}. Searching jobs in the background; the list refreshes only when new jobs or updated results appear. You can keep working while search continues."
                ),
            )
        )

        def _task() -> Any:
            return self.runner.run_search(
                candidate=candidate,
                profiles=active_profiles,
                settings=self.context.settings.get_effective_openai_settings(),
                api_base_url=self.context.settings.get_openai_base_url(),
                cancel_event=self._search_cancel_event,
            )

        def _on_success(result: Any) -> None:
            if self.current_candidate_id != candidate_id:
                return
            if not hasattr(result, "success"):
                QMessageBox.warning(
                    self,
                    _t(self.ui_language, "岗位搜索结果", "Search Results"),
                    _t(self.ui_language, "运行结果格式异常。", "Unexpected run result payload."),
                )
                return
            if getattr(result, "cancelled", False):
                jobs = self.runner.load_recommended_jobs(candidate_id)
                visible_count = self._render_jobs(jobs)
                self._refresh_results_stats_label()
                self.results_meta_label.setText(
                    _t(
                        self.ui_language,
                        f"当前求职者：{candidate.name}。后台搜索已停止，当前保留已完成落盘的结果（{visible_count} 条）。",
                        f"Current candidate: {candidate.name}. Background search stopped. Any fully persisted results have been kept ({visible_count} item(s)).",
                    )
                )
                self._show_notification_toast(
                    _t(
                        self.ui_language,
                        f"岗位搜索已停止，当前保留 {visible_count} 条结果。",
                        f"Job search stopped. Kept {visible_count} result(s).",
                    ),
                    level="warning",
                    duration_ms=4500,
                )
                return
            if not result.success:
                details = result.stderr_tail or result.stdout_tail or "No logs."
                self.results_meta_label.setText(
                    _t(
                        self.ui_language,
                        f"当前求职者：{candidate.name}。运行失败：{result.message}",
                        f"Current candidate: {candidate.name}. Run failed: {result.message}",
                    )
                )
                QMessageBox.warning(
                    self,
                    _t(self.ui_language, "岗位搜索结果", "Search Results"),
                    f"{result.message}\n\nExit code: {result.exit_code}\n\n{details}",
                )
                return

            jobs = self.runner.load_recommended_jobs(candidate_id)
            visible_count = self._render_jobs(jobs)
            self._refresh_results_stats_label()
            pending_after_run = self._main_pending_analysis_count(candidate_id)
            self.results_meta_label.setText(
                _t(
                    self.ui_language,
                    (
                        f"当前求职者：{candidate.name}。搜索已完成，当前展示 {visible_count} 条结果；"
                        f"仍有 {pending_after_run} 条主流程岗位待补完，下次点击“寻找更多岗位”时会优先继续处理。"
                        if pending_after_run > 0
                        else f"当前求职者：{candidate.name}。搜索已完成，当前展示 {visible_count} 条结果。"
                    ),
                    (
                        f"Current candidate: {candidate.name}. Search finished, now showing {visible_count} result(s); "
                        f"{pending_after_run} main-stage job(s) are still pending and will resume first the next time you click 'Find More Jobs'."
                        if pending_after_run > 0
                        else f"Current candidate: {candidate.name}. Search finished, now showing {visible_count} result(s)."
                    ),
                )
            )
            self._show_notification_toast(
                (
                    _t(
                        self.ui_language,
                        f"岗位搜索已完成，当前展示 {visible_count} 条结果。",
                        f"Job search finished. Showing {visible_count} result(s).",
                    )
                    if visible_count > 0
                    else _t(
                        self.ui_language,
                        "岗位搜索已完成，当前没有可展示结果。",
                        "Job search finished. No displayable results yet.",
                    )
                ),
                level="success",
                duration_ms=5000,
            )

        def _on_error(exc: Exception) -> None:
            if self.current_candidate_id == candidate_id:
                self.results_meta_label.setText(
                    _t(
                        self.ui_language,
                        f"当前求职者：{candidate.name}。运行失败：{exc}",
                        f"Current candidate: {candidate.name}. Run failed: {exc}",
                    )
                )
                self._refresh_results_stats_label()
            QMessageBox.warning(
                self,
                _t(self.ui_language, "岗位搜索结果", "Search Results"),
                str(exc),
            )

        def _on_finally() -> None:
            self._stop_live_results_updates()
            self.refresh_button.setEnabled(self.current_candidate_id is not None)
            self.stop_button.setEnabled(False)
            self._search_cancel_event = None
            self._refresh_results_stats_label()

        started = run_busy_task(
            self,
            title=_t(self.ui_language, "岗位搜索结果", "Search Results"),
            message=_t(
                self.ui_language,
                "系统正在后台搜索岗位；只有发现新岗位或结果变化时才会刷新列表。你可以继续操作，搜索会在后台持续运行。",
                "Searching jobs in the background; the list refreshes only when new jobs or updated results appear. You can keep working while search continues.",
            ),
            task=_task,
            on_success=_on_success,
            on_error=_on_error,
            on_finally=_on_finally,
            show_dialog=False,
        )
        if not started:
            self.refresh_button.setEnabled(self.current_candidate_id is not None)
            self.stop_button.setEnabled(False)
            self._search_cancel_event = None
            self._stop_live_results_updates()
            return
        self._start_live_results_updates(candidate_id)

    def _visible_jobs(self, jobs: list[LegacyJobResult]) -> list[LegacyJobResult]:
        sorted_jobs = sorted(
            jobs,
            key=lambda item: self._parse_date_found(item.date_found),
            reverse=True,
        )
        return [item for item in sorted_jobs if self._job_key(item) not in self.hidden_job_keys]

    def _job_render_signature(self, job: LegacyJobResult) -> tuple[object, ...]:
        detail_url, final_url, link_status = self._job_link_details(job)
        return (
            str(job.url or "").strip().casefold(),
            detail_url.casefold(),
            final_url.casefold(),
            link_status.casefold(),
            str(job.title or "").strip(),
            str(job.company or "").strip(),
            str(job.location or "").strip(),
            str(job.date_found or "").strip(),
            job.match_score,
            bool(job.recommend),
            str(job.fit_level_cn or "").strip(),
            str(job.fit_track or "").strip(),
            str(job.adjacent_direction_cn or "").strip(),
        )

    def _jobs_signature(self, jobs: list[LegacyJobResult]) -> tuple[tuple[object, ...], ...]:
        return tuple(self._job_render_signature(job) for job in jobs)

    def _sync_live_results_signature(self) -> None:
        if self.current_candidate_id is None:
            self._live_results_signature = ()
            return
        jobs = self.runner.load_live_jobs(self.current_candidate_id)
        if not jobs:
            jobs = self.runner.load_recommended_jobs(self.current_candidate_id)
        self._live_results_signature = self._jobs_signature(self._visible_jobs(jobs))

    def _main_pending_analysis_count(self, candidate_id: int | None = None) -> int:
        target_candidate_id = candidate_id if candidate_id is not None else self.current_candidate_id
        if target_candidate_id is None:
            return 0
        try:
            stats = self.runner.load_search_stats(int(target_candidate_id))
        except Exception:
            return 0
        return max(0, int(getattr(stats, "main_pending_analysis_count", 0) or 0))

    @staticmethod
    def _format_elapsed_text(seconds: int) -> str:
        total = max(0, int(seconds or 0))
        minutes, remaining_seconds = divmod(total, 60)
        hours, remaining_minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h {remaining_minutes}m {remaining_seconds}s"
        if minutes > 0:
            return f"{minutes}m {remaining_seconds}s"
        return f"{remaining_seconds}s"

    def _search_progress_text(self, candidate_id: int | None) -> tuple[str, str]:
        if candidate_id is None:
            return "", ""
        try:
            progress = self.runner.load_search_progress(int(candidate_id))
        except Exception:
            return "", ""
        if str(getattr(progress, "status", "") or "").strip().lower() != "running":
            return "", ""

        stage = str(getattr(progress, "stage", "") or "").strip().lower()
        stage_label = {
            "preparing": _t(self.ui_language, "准备环境", "Preparing"),
            "resume_pending": _t(self.ui_language, "补完待处理岗位", "Resuming pending jobs"),
            "web_signal": _t(self.ui_language, "初始信号搜索", "Web signal"),
            "main": _t(self.ui_language, "主流程分析", "Main stage"),
            "completed": _t(self.ui_language, "已完成", "Completed"),
        }.get(stage, _t(self.ui_language, "后台处理中", "Background work"))
        elapsed_text = self._format_elapsed_text(int(getattr(progress, "elapsed_seconds", 0) or 0))
        detail = str(getattr(progress, "last_event", "") or getattr(progress, "message", "") or "").strip()
        dialog_text = _t(
            self.ui_language,
            f"系统正在后台搜索岗位，当前阶段：{stage_label}，已运行 {elapsed_text}。你可以继续操作，搜索会在后台持续运行。",
            f"Searching jobs in the background. Current stage: {stage_label}, elapsed {elapsed_text}. You can keep working while search continues.",
        )
        if detail:
            page_text = _t(
                self.ui_language,
                f"当前求职者：{self.current_candidate_name}。后台正在{stage_label}，已运行 {elapsed_text}。最新进度：{detail}",
                f"Current candidate: {self.current_candidate_name}. Background work is in {stage_label}, elapsed {elapsed_text}. Latest progress: {detail}",
            )
        else:
            page_text = _t(
                self.ui_language,
                f"当前求职者：{self.current_candidate_name}。后台正在{stage_label}，已运行 {elapsed_text}。",
                f"Current candidate: {self.current_candidate_name}. Background work is in {stage_label}, elapsed {elapsed_text}.",
            )
        return page_text, dialog_text

    def _set_busy_task_message(self, text: str) -> None:
        dialog = getattr(self, "_busy_task_dialog", None)
        if isinstance(dialog, QProgressDialog) and str(text or "").strip():
            dialog.setLabelText(text)

    def _hide_notification_toast(self) -> None:
        toast = self._notification_toast
        self._notification_toast = None
        if isinstance(toast, QFrame):
            toast.hide()
            toast.deleteLater()

    def _show_notification_toast(self, text: str, level: str = "info", duration_ms: int = 5000) -> None:
        message = str(text or "").strip()
        if not message:
            return
        self._notification_timer.stop()
        self._hide_notification_toast()

        host = self.window() if isinstance(self.window(), QWidget) else self
        background = "#102a43"
        border = "#1f7a8c"
        if level == "success":
            background = "#0f5132"
            border = "#198754"
        elif level == "warning":
            background = "#5c3900"
            border = "#f59f00"
        elif level == "error":
            background = "#58151c"
            border = "#dc3545"

        toast = QFrame(host)
        toast.setFrameShape(QFrame.StyledPanel)
        toast.setStyleSheet(
            f"background: {background}; border: 1px solid {border}; border-radius: 12px; color: #f8fafc;"
        )
        layout = QVBoxLayout(toast)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(0)
        label = QLabel(message, toast)
        label.setWordWrap(True)
        label.setStyleSheet("color: #f8fafc; background: transparent;")
        layout.addWidget(label)
        toast.setMaximumWidth(420)
        toast.adjustSize()
        host_rect = host.rect()
        x = max(16, host_rect.width() - toast.width() - 20)
        y = max(16, host_rect.height() - toast.height() - 28)
        toast.move(x, y)
        toast.show()
        toast.raise_()
        self._notification_toast = toast
        self._notification_timer.start(max(1500, int(duration_ms)))

        if hasattr(host, "statusBar"):
            try:
                host.statusBar().showMessage(message, max(1500, int(duration_ms)))
            except Exception:
                pass

    def _render_jobs(self, jobs: list[LegacyJobResult]) -> int:
        return self._render_visible_jobs(self._visible_jobs(jobs))

    def _render_visible_jobs(self, visible_jobs: list[LegacyJobResult]) -> int:
        self.table.setRowCount(0)
        for row_index, job in enumerate(visible_jobs):
            job_key = self._job_key(job)
            detail_url, _final_url, _link_status = self._job_link_details(job)
            self.table.insertRow(row_index)
            title_item = QTableWidgetItem(job.title)
            title_item.setData(Qt.UserRole, job_key)
            self.table.setItem(row_index, 0, title_item)
            self.table.setItem(row_index, 1, QTableWidgetItem(self._infer_target_role(job)))
            self.table.setItem(row_index, 2, QTableWidgetItem(job.company))
            self.table.setItem(row_index, 3, QTableWidgetItem(job.location))

            self.table.setCellWidget(row_index, 4, self._make_link_cell(detail_url, True))
            self.table.setItem(row_index, 5, QTableWidgetItem(job.date_found))
            self.table.setItem(row_index, 6, QTableWidgetItem(self._format_score(job)))

            status_combo = QComboBox()
            status_labels = [self._status_display(code) for code in self.STATUS_CODES]
            status_combo.addItems(status_labels)
            self._decorate_status_combo_items(status_combo)
            current_status_code = self.status_by_job_key.get(job_key, "pending")
            current_index = self.STATUS_CODES.index(current_status_code) if current_status_code in self.STATUS_CODES else 0
            status_combo.setCurrentIndex(current_index)
            self._apply_status_combo_style(status_combo, current_status_code)
            status_combo.currentTextChanged.connect(
                lambda text, key=job_key, combo=status_combo: self._on_status_changed(key, text, combo)
            )
            self.table.setCellWidget(row_index, 7, status_combo)
        self._live_results_signature = self._jobs_signature(visible_jobs)
        return len(visible_jobs)

    @staticmethod
    def _first_non_empty(*values: Any) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _link_host_text(url: str, fallback: str) -> str:
        text = str(url or "").strip()
        if not text:
            return fallback
        parsed = urlparse(text)
        host = str(parsed.netloc or "").replace("www.", "").strip()
        if host:
            return host if len(host) <= 32 else f"{host[:31]}…"
        path = str(parsed.path or "").strip("/")
        if path:
            tail = path.rsplit("/", 1)[-1].strip()
            if tail:
                return tail if len(tail) <= 32 else f"{tail[:31]}…"
        return fallback

    @staticmethod
    def _make_link_widget(url: str, label: str) -> QLabel:
        safe_url = str(url or "").strip()
        safe_label = str(label or "").strip() or safe_url
        if safe_url:
            widget = QLabel(f'<a href="{escape(safe_url, quote=True)}">{escape(safe_label)}</a>')
            widget.setOpenExternalLinks(True)
            widget.setTextFormat(Qt.RichText)
        else:
            widget = QLabel(safe_label)
            widget.setTextFormat(Qt.PlainText)
        widget.setTextInteractionFlags(Qt.TextBrowserInteraction)
        widget.setAlignment(Qt.AlignCenter)
        widget.setToolTip(safe_url or safe_label)
        return widget

    def _make_link_cell(self, url: str, prefer_detail_label: bool) -> QLabel:
        if not str(url or "").strip():
            return self._make_link_widget("", "-")
        fallback_label = _t(self.ui_language, "打开链接", "Open link")
        label = self._link_host_text(url, fallback_label)
        if prefer_detail_label:
            label = _t(self.ui_language, f"详情 · {label}", f"Details · {label}")
        return self._make_link_widget(url, label)

    def _job_link_details(self, job: LegacyJobResult) -> tuple[str, str, str]:
        detail_url = self._first_non_empty(
            getattr(job, "source_url", ""),
            getattr(job, "sourceUrl", ""),
            getattr(job, "original_url", ""),
            getattr(job, "originalUrl", ""),
            getattr(job, "canonicalUrl", ""),
            getattr(job, "url", ""),
        )
        final_url = self._first_non_empty(
            getattr(job, "final_url", ""),
            getattr(job, "finalUrl", ""),
            getattr(job, "apply_url", ""),
            getattr(job, "applyUrl", ""),
            getattr(job, "apply_link", ""),
            getattr(job, "applyLink", ""),
            getattr(job, "application_url", ""),
            getattr(job, "applicationUrl", ""),
            getattr(job, "canonicalUrl", ""),
            getattr(job, "url", ""),
            detail_url,
        )
        if not detail_url:
            detail_url = final_url
        if not final_url:
            final_url = detail_url
        link_status = self._first_non_empty(
            getattr(job, "link_status", ""),
            getattr(job, "linkStatus", ""),
            getattr(job, "final_url_status", ""),
            getattr(job, "finalUrlStatus", ""),
            getattr(job, "apply_url_status", ""),
            getattr(job, "applyUrlStatus", ""),
            getattr(job, "source_url_status", ""),
            getattr(job, "sourceUrlStatus", ""),
            getattr(job, "url_status", ""),
            getattr(job, "urlStatus", ""),
            getattr(job, "link_state", ""),
            getattr(job, "linkState", ""),
            getattr(job, "link_state_cn", ""),
            getattr(job, "linkStateCn", ""),
            getattr(job, "link_state_label", ""),
            getattr(job, "linkStateLabel", ""),
        )
        return detail_url, final_url, link_status

    def _start_live_results_updates(self, candidate_id: int) -> None:
        self._live_results_candidate_id = candidate_id
        self._live_results_last_count = -1
        self._refresh_live_results()
        self._live_results_timer.start()

    def _stop_live_results_updates(self) -> None:
        self._live_results_timer.stop()
        self._live_results_candidate_id = None

    def _refresh_live_results(self) -> None:
        candidate_id = self._live_results_candidate_id
        if candidate_id is None or self.current_candidate_id != candidate_id:
            return
        self._refresh_results_stats_label()
        progress_page_text, progress_dialog_text = self._search_progress_text(candidate_id)
        if progress_dialog_text:
            self._set_busy_task_message(progress_dialog_text)
        jobs = self.runner.load_live_jobs(candidate_id)
        visible_jobs = self._visible_jobs(jobs)
        if visible_jobs:
            signature = self._jobs_signature(visible_jobs)
            visible_count = len(visible_jobs)
            if signature != self._live_results_signature:
                visible_count = self._render_visible_jobs(visible_jobs)
                self._live_results_last_count = visible_count
            message = progress_page_text or _t(
                self.ui_language,
                f"当前求职者：{self.current_candidate_name}。后台正在搜索与分析，已发现 {visible_count} 条临时结果；只有出现新增或内容变化时才会刷新列表。",
                f"Current candidate: {self.current_candidate_name}. Search and analysis are still running in the background; {visible_count} interim result(s) found so far, and the list will refresh only when something changes.",
            )
            if self.results_meta_label.text() != message:
                self.results_meta_label.setText(message)
            return
        if self._live_results_last_count > 0:
            return
        message = progress_page_text or _t(
            self.ui_language,
            f"当前求职者：{self.current_candidate_name}。后台正在搜索；若暂时没有新增岗位，通常表示系统仍在抓取、分析或收尾。只要搜索提示框还在，后台任务就仍在运行。",
            f"Current candidate: {self.current_candidate_name}. Search is still running in the background; if no new jobs appear yet, the system is usually still collecting, analyzing, or finishing. As long as the search dialog remains open, the background task is still running.",
        )
        if self.results_meta_label.text() != message:
            self.results_meta_label.setText(message)

    def _refresh_results_stats_label(self) -> None:
        candidate_id = self.current_candidate_id
        if candidate_id is None:
            text = _t(
                self.ui_language,
                "内部统计：未选择求职者。",
                "Internal stats: no candidate selected.",
            )
        else:
            try:
                stats = self.runner.load_search_stats(candidate_id)
            except Exception as exc:
                text = _t(
                    self.ui_language,
                    f"内部统计读取失败：{exc}",
                    f"Failed to read internal stats: {exc}",
                )
            else:
                summary = _t(
                    self.ui_language,
                    (
                        f"内部统计：当前候选公司池 {stats.candidate_company_pool_count} 家。\n"
                        f"Signal 命中 {stats.signal_hit_job_count} 条；"
                        f"主流程已发现 {stats.main_discovered_job_count} 条，"
                        f"已评分 {stats.main_scored_job_count} 条，"
                        f"当前展示 {stats.displayable_result_count} 条，"
                        f"待补完 {stats.main_pending_analysis_count} 条。"
                    ),
                    (
                        f"Internal stats: current candidate pool {stats.candidate_company_pool_count}.\n"
                        f"Signal hits {stats.signal_hit_job_count}; "
                        f"main-stage discovered {stats.main_discovered_job_count}, "
                        f"scored {stats.main_scored_job_count}, "
                        f"displayed {stats.displayable_result_count}, "
                        f"pending completion {stats.main_pending_analysis_count}."
                    ),
                )
                if (
                    stats.signal_hit_job_count == 0
                    and stats.main_discovered_job_count == 0
                    and stats.main_pending_analysis_count == 0
                ):
                    summary = summary + _t(
                        self.ui_language,
                        " 当前运行目录里还没有已落盘的岗位结果。",
                        " No persisted job results in the current run directory yet.",
                    )
                text = summary
        if self.results_stats_label.text() != text:
            self.results_stats_label.setText(text)

    def _build_target_role_candidates(self, profiles: list[SearchProfileRecord]) -> list[str]:
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

    def _infer_target_role(self, job: LegacyJobResult) -> str:
        if not self.target_role_candidates:
            return _t(self.ui_language, "未设置", "Unmapped")
        haystack = " ".join(
            [
                str(job.title or ""),
                str(job.fit_track or ""),
                str(job.adjacent_direction_cn or ""),
                str(job.fit_level_cn or ""),
            ]
        ).casefold()
        hay_tokens = set(re.findall(r"[a-z0-9]+", haystack))

        best_role = self.target_role_candidates[0]
        best_score = -1
        for role in self.target_role_candidates:
            role_lower = role.casefold()
            role_tokens = set(re.findall(r"[a-z0-9]+", role_lower))
            token_score = len(hay_tokens & role_tokens) * 10
            contains_score = 6 if role_lower and role_lower in haystack else 0
            char_score = len(set(role_lower) & set(haystack))
            score = token_score + contains_score + char_score
            if score > best_score:
                best_score = score
                best_role = role
        return best_role

    def _format_score(self, job: LegacyJobResult) -> str:
        score = job.match_score
        if score is None:
            return _t(self.ui_language, "无评分", "No score")
        if score >= 85:
            level = _t(self.ui_language, "高推荐", "High")
        elif score >= 70:
            level = _t(self.ui_language, "中推荐", "Medium")
        else:
            level = _t(self.ui_language, "低推荐", "Low")
        if self.ui_language == "en":
            return f"{score} ({level})"
        return f"{score}（{level}）"

    @staticmethod
    def _parse_date_found(value: str) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        normalized = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return 0.0

    @staticmethod
    def _job_key(job: LegacyJobResult) -> str:
        if job.url.strip():
            return job.url.strip().casefold()
        return f"{job.title}|{job.company}|{job.date_found}".casefold()

    def _status_store_key(self) -> str:
        candidate_id = self.current_candidate_id or 0
        return f"search_results_statuses_candidate_{candidate_id}"

    def _hidden_store_key(self) -> str:
        candidate_id = self.current_candidate_id or 0
        return f"search_results_hidden_candidate_{candidate_id}"

    def _load_review_state(self, candidate_id: int) -> None:
        raw_statuses = self.context.settings.get_value(
            f"search_results_statuses_candidate_{candidate_id}",
            "{}",
        )
        raw_hidden = self.context.settings.get_value(
            f"search_results_hidden_candidate_{candidate_id}",
            "[]",
        )
        try:
            status_map = json.loads(raw_statuses)
        except Exception:
            status_map = {}
        try:
            hidden_list = json.loads(raw_hidden)
        except Exception:
            hidden_list = []
        if isinstance(status_map, dict):
            normalized_statuses: dict[str, str] = {}
            for key, value in status_map.items():
                status_code = self._normalize_status_code(str(value))
                if status_code:
                    normalized_statuses[str(key)] = status_code
            self.status_by_job_key = normalized_statuses
        else:
            self.status_by_job_key = {}
        if isinstance(hidden_list, list):
            self.hidden_job_keys = {str(item) for item in hidden_list if str(item).strip()}
        else:
            self.hidden_job_keys = set()

    def _save_review_state(self) -> None:
        if self.current_candidate_id is None:
            return
        self.context.settings.set_value(
            self._status_store_key(),
            json.dumps(self.status_by_job_key, ensure_ascii=False),
        )
        self.context.settings.set_value(
            self._hidden_store_key(),
            json.dumps(sorted(self.hidden_job_keys), ensure_ascii=False),
        )

    def _on_status_changed(self, job_key: str, status_text: str, combo: QComboBox | None = None) -> None:
        status_code = self._normalize_status_code(status_text)
        if status_code is None:
            return
        self.status_by_job_key[job_key] = status_code
        if combo is not None:
            self._apply_status_combo_style(combo, status_code)
        self._save_review_state()

    def _delete_selected_rows(self) -> None:
        selected_rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        if not selected_rows and self.table.currentRow() >= 0:
            selected_rows = [self.table.currentRow()]
        if not selected_rows:
            QMessageBox.information(
                self,
                _t(self.ui_language, "岗位搜索结果", "Search Results"),
                _t(self.ui_language, "请先选中要删除的岗位行。", "Please select row(s) to delete first."),
            )
            return
        for row in selected_rows:
            key_item = self.table.item(row, 0)
            job_key = str(key_item.data(Qt.UserRole) or "").strip() if key_item is not None else ""
            if job_key:
                self.hidden_job_keys.add(job_key)
                self.status_by_job_key.pop(job_key, None)
            self.table.removeRow(row)
        self._save_review_state()
        self._live_results_last_count = self.table.rowCount()
        self._sync_live_results_signature()
        self.results_meta_label.setText(
            _t(
                self.ui_language,
                f"当前求职者：{self.current_candidate_name}。已删除 {len(selected_rows)} 条岗位，当前展示 {self.table.rowCount()} 条。",
                f"Current candidate: {self.current_candidate_name}. Deleted {len(selected_rows)} row(s), now showing {self.table.rowCount()} row(s).",
            )
        )

    def _status_display(self, status_code: str) -> str:
        labels = {
            "pending": _t(self.ui_language, "待定", "Pending"),
            "focus": _t(self.ui_language, "重点", "Focus"),
            "applied": _t(self.ui_language, "已投递", "Applied"),
            "offered": _t(self.ui_language, "已得到 Offer", "Offer Received"),
            "rejected": _t(self.ui_language, "已被拒绝", "Rejected"),
            "dropped": _t(self.ui_language, "已放弃", "Dropped"),
        }
        return labels.get(status_code, labels["pending"])

    def _status_palette(self, status_code: str) -> dict[str, str]:
        palette = {
            "pending": {"fg": "#4B5563", "bg": "#F3F4F6", "border": "#9CA3AF"},
            "focus": {"fg": "#1D4ED8", "bg": "#DBEAFE", "border": "#3B82F6"},
            "applied": {"fg": "#166534", "bg": "#DCFCE7", "border": "#22C55E"},
            "offered": {"fg": "#92400E", "bg": "#FEF3C7", "border": "#F59E0B"},
            "rejected": {"fg": "#991B1B", "bg": "#FEE2E2", "border": "#EF4444"},
            "dropped": {"fg": "#334155", "bg": "#E2E8F0", "border": "#94A3B8"},
        }
        return palette.get(status_code, palette["pending"])

    def _decorate_status_combo_items(self, combo: QComboBox) -> None:
        model = combo.model()
        if model is None:
            return
        for index, status_code in enumerate(self.STATUS_CODES):
            color_pack = self._status_palette(status_code)
            model_index = model.index(index, 0)
            model.setData(model_index, QBrush(QColor(color_pack["fg"])), Qt.ForegroundRole)
            model.setData(model_index, QBrush(QColor(color_pack["bg"])), Qt.BackgroundRole)

    def _apply_status_combo_style(self, combo: QComboBox, status_code: str) -> None:
        color_pack = self._status_palette(status_code)
        combo.setStyleSheet(
            f"""
            QComboBox {{
                color: {color_pack["fg"]};
                background-color: {color_pack["bg"]};
                border: 1px solid {color_pack["border"]};
                border-radius: 6px;
                padding: 2px 8px;
                min-height: 24px;
            }}
            QComboBox QAbstractItemView {{
                color: #0F172A;
                background: #FFFFFF;
                border: 1px solid {color_pack["border"]};
                selection-background-color: {color_pack["bg"]};
                selection-color: {color_pack["fg"]};
            }}
            """
        )

    def _normalize_status_code(self, value: str) -> str | None:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None
        if normalized in self.STATUS_CODES:
            return normalized
        map_by_label = {
            "待定": "pending",
            "pending": "pending",
            "重点": "focus",
            "focus": "focus",
            "已投递": "applied",
            "applied": "applied",
            "已得到 offer": "offered",
            "已得到offer": "offered",
            "已得到 Offer": "offered",
            "offer received": "offered",
            "offered": "offered",
            "已被拒绝": "rejected",
            "rejected": "rejected",
            "已放弃": "dropped",
            "dropped": "dropped",
        }
        return map_by_label.get(value.strip()) or map_by_label.get(normalized)


class CandidateWorkspacePage(QWidget):
    def __init__(
        self,
        context: AppContext,
        ui_language: str = "zh",
        on_data_changed: Callable[[], None] | None = None,
        on_back_to_candidates: Callable[[], None] | None = None,
        on_ui_language_changed: Callable[[str], None] | None = None,
        on_ai_settings_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.context = context
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.on_data_changed = on_data_changed
        self.on_back_to_candidates = on_back_to_candidates
        self.on_ui_language_changed = on_ui_language_changed
        self.on_ai_settings_changed = on_ai_settings_changed
        self.current_candidate_id: int | None = None
        self.step_buttons: list[QPushButton] = []
        self.step_titles = [
            _t(self.ui_language, "基础信息", "Basics"),
            _t(self.ui_language, "目标岗位设立", "Target Roles"),
            _t(self.ui_language, "岗位搜索结果", "Search Results"),
        ]

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(20, 20, 20, 20)
        outer_layout.setSpacing(16)
        outer_layout.addWidget(
            make_page_title(
                _t(self.ui_language, "求职者工作台", "Candidate Workspace"),
                _t(
                    self.ui_language,
                    "进入之后就是这个人的单独工作台。先填基础信息，再设定目标岗位方向，然后直接查看岗位搜索结果。",
                    "This is the candidate-specific workspace. Fill basics, set target roles, then review search results.",
                ),
            )
        )

        self.body_stack = QStackedWidget()
        outer_layout.addWidget(self.body_stack, 1)

        self.empty_page = make_card()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(24, 24, 24, 24)
        empty_layout.setSpacing(12)
        empty_title = QLabel(_t(self.ui_language, "还没有选中求职者", "No Candidate Selected"))
        empty_title.setObjectName("PageTitle")
        empty_subtitle = QLabel(
            _t(
                self.ui_language,
                "请先在启动页选择一个求职者，然后再进入这个工作台。",
                "Select a candidate first, then enter this workspace.",
            )
        )
        empty_subtitle.setObjectName("PageSubtitle")
        empty_subtitle.setWordWrap(True)
        self.go_candidates_button = styled_button(
            _t(self.ui_language, "返回求职者选择", "Back to Candidates"),
            "primary",
        )
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_subtitle)
        empty_layout.addWidget(self.go_candidates_button, 0, Qt.AlignLeft)
        empty_layout.addStretch(1)

        self.content_page = QWidget()
        content_layout = QVBoxLayout(self.content_page)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        self.hero_card = make_card()
        self.hero_card.setObjectName("WorkspaceHero")
        hero_layout = QHBoxLayout(self.hero_card)
        hero_layout.setContentsMargins(22, 20, 22, 20)
        hero_layout.setSpacing(18)

        hero_text = QWidget()
        hero_text_layout = QVBoxLayout(hero_text)
        hero_text_layout.setContentsMargins(0, 0, 0, 0)
        hero_text_layout.setSpacing(6)
        self.hero_eyebrow = QLabel(_t(self.ui_language, "当前求职者", "Current Candidate"))
        self.hero_eyebrow.setObjectName("HeroEyebrow")
        self.hero_title = QLabel(_t(self.ui_language, "未选择求职者", "No Candidate Selected"))
        self.hero_title.setObjectName("HeroTitle")
        self.hero_meta = QLabel("")
        self.hero_meta.setObjectName("HeroMeta")
        self.hero_meta.setWordWrap(True)
        self.ai_validation_status_label = QLabel("")
        self.ai_validation_status_label.setObjectName("HeroAiStatus")
        self.ai_validation_status_label.setWordWrap(True)
        hero_text_layout.addWidget(self.hero_eyebrow)
        hero_text_layout.addWidget(self.hero_title)
        hero_text_layout.addWidget(self.hero_meta)
        hero_text_layout.addWidget(self.ai_validation_status_label)
        hero_layout.addWidget(hero_text, 1)

        self.switch_candidate_button = styled_button(
            _t(self.ui_language, "更换求职者", "Switch Candidate"),
            "hero",
        )
        self.workspace_settings_button = styled_button(
            _t(self.ui_language, "AI 设置", "AI Settings"),
            "hero",
        )
        self.support_button = styled_button(
            _t(self.ui_language, "☕ 支持开发", "☕ Support Dev"),
            "hero",
        )
        self.support_button.setToolTip(
            _t(
                self.ui_language,
                "如果这个工具帮到了你，可以给开发者买杯咖啡。",
                "If this tool helps you, you can buy the developer a coffee.",
            )
        )
        hero_actions = QWidget()
        hero_actions_layout = QVBoxLayout(hero_actions)
        hero_actions_layout.setContentsMargins(0, 0, 0, 0)
        hero_actions_layout.setSpacing(8)
        hero_actions_layout.addWidget(self.support_button)
        hero_actions_layout.addWidget(self.workspace_settings_button)
        hero_actions_layout.addWidget(self.switch_candidate_button)
        hero_actions_layout.addStretch(1)
        hero_layout.addWidget(hero_actions, 0, Qt.AlignTop)
        content_layout.addWidget(self.hero_card)

        step_card = make_card()
        step_layout = QHBoxLayout(step_card)
        step_layout.setContentsMargins(14, 14, 14, 14)
        step_layout.setSpacing(10)
        for index, title in enumerate(self.step_titles):
            button = styled_button(f"{index + 1}. {title}", "step")
            button.clicked.connect(lambda _checked=False, step_index=index: self._set_step(step_index))
            self.step_buttons.append(button)
            step_layout.addWidget(button)
        content_layout.addWidget(step_card)

        self.step_stack = QStackedWidget()
        self.basics_step = CandidateBasicsStep(
            context,
            ui_language=self.ui_language,
            on_data_changed=on_data_changed,
            on_candidate_saved=self._on_candidate_saved,
        )
        self.strategy_step = TargetDirectionStep(
            context,
            ui_language=self.ui_language,
            on_data_changed=on_data_changed,
        )
        self.results_step = SearchResultsStep(context, ui_language=self.ui_language)
        self.step_stack.addWidget(make_scroll_area(self.basics_step))
        self.step_stack.addWidget(make_scroll_area(self.strategy_step))
        self.step_stack.addWidget(make_scroll_area(self.results_step))
        content_layout.addWidget(self.step_stack, 1)

        self.body_stack.addWidget(self.empty_page)
        self.body_stack.addWidget(self.content_page)

        self.go_candidates_button.clicked.connect(self._go_back_to_candidates)
        self.switch_candidate_button.clicked.connect(self._go_back_to_candidates)
        self.workspace_settings_button.clicked.connect(self._open_ai_settings)
        self.support_button.clicked.connect(self._show_support_dialog)
        self._set_step(0)
        self.set_ai_validation_status(
            _t(self.ui_language, "AI 状态：等待验证", "AI status: waiting for validation"),
            "idle",
        )
        self.set_candidate(None)

    def set_ai_validation_status(self, message: str, level: str = "idle") -> None:
        dot_palette = {
            "idle": "#94a3b8",
            "checking": "#2563eb",
            "ready": "#15803d",
            "switched": "#15803d",
            "missing": "#b91c1c",
            "warning": "#b91c1c",
            "invalid": "#b91c1c",
            "model_unverified": "#b91c1c",
            "success": "#15803d",
            "error": "#b91c1c",
        }
        dot_color = dot_palette.get(level, dot_palette["idle"])
        safe_message = escape(str(message or "").strip())
        self.ai_validation_status_label.setText(
            f'<span style="color: {dot_color}; font-size: 15px;">●</span> '
            f'<span style="color: #ffffff;">{safe_message}</span>'
        )
        self.ai_validation_status_label.setStyleSheet("color: #ffffff;")

    def set_candidate(self, candidate_id: int | None) -> None:
        self.current_candidate_id = candidate_id
        if candidate_id is None:
            self.body_stack.setCurrentWidget(self.empty_page)
            self.basics_step.set_candidate(None)
            self.strategy_step.set_candidate(None)
            self.results_step.set_candidate(None)
            return

        candidate = self.context.candidates.get(candidate_id)
        if candidate is None:
            self.current_candidate_id = None
            self.body_stack.setCurrentWidget(self.empty_page)
            self.basics_step.set_candidate(None)
            self.strategy_step.set_candidate(None)
            self.results_step.set_candidate(None)
            return

        profile_count = len(self.context.profiles.list_for_candidate(candidate_id))
        resume_name = Path(candidate.active_resume_path).name if candidate.active_resume_path else _t(
            self.ui_language,
            "未设置简历",
            "No resume",
        )
        base_location = candidate.base_location or _t(
            self.ui_language,
            "未填写当前所在地",
            "Location not set",
        )
        self.hero_title.setText(candidate.name)
        if self.ui_language == "en":
            self.hero_meta.setText(
                f"Location: {base_location}    ·    Resume: {resume_name}    ·    Roles: {profile_count}"
            )
        else:
            self.hero_meta.setText(
                f"当前所在地：{base_location}    ·    简历：{resume_name}    ·    当前岗位数：{profile_count}"
            )

        self.basics_step.set_candidate(candidate_id)
        self.strategy_step.set_candidate(candidate_id)
        self.results_step.set_candidate(candidate)
        self.body_stack.setCurrentWidget(self.content_page)

    def _set_step(self, index: int) -> None:
        self.step_stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.step_buttons):
            active = button_index == index
            button.setProperty("activeStep", active)
            button.style().unpolish(button)
            button.style().polish(button)

    def _on_candidate_saved(self, candidate_id: int) -> None:
        self.set_candidate(candidate_id)

    def _go_back_to_candidates(self) -> None:
        if self.on_back_to_candidates:
            self.on_back_to_candidates()

    def _support_paypal_email(self) -> str:
        env_email = os.environ.get(SUPPORT_PAYPAL_EMAIL_ENV, "").strip()
        if env_email:
            return env_email
        saved_email = self.context.settings.get_value(SUPPORT_PAYPAL_EMAIL_SETTING_KEY, "").strip()
        if saved_email:
            return saved_email
        return SUPPORT_PAYPAL_EMAIL_DEFAULT

    def _show_support_dialog(self) -> None:
        paypal_email = self._support_paypal_email()
        title = _t(self.ui_language, "支持开发", "Support Development")
        message = _t(
            self.ui_language,
            "这个工具的开发和维护用了大量 Codex 与本地调试成本。如果它真的帮到了你，欢迎给开发者买杯咖啡。",
            "Building and maintaining this tool takes substantial Codex usage and local debugging cost. If it genuinely helps you, you are welcome to buy the developer a coffee.",
        )
        info_lines = [
            message,
            "",
        ]
        if paypal_email:
            info_lines.extend(
                [
                    _t(
                        self.ui_language,
                        "可通过 PayPal 向下面这个账号转账：",
                        "You can send support via PayPal to this account:",
                    ),
                    paypal_email,
                ]
            )
        else:
            info_lines.append(
                _t(
                    self.ui_language,
                    "PayPal 账号暂未配置。你之后给我邮箱地址后，我可以再帮你直接写进去。",
                    "PayPal account is not configured yet. Once you provide the email address, I can wire it in directly.",
                )
            )

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.NoIcon)
        dialog.setWindowTitle(title)
        dialog.setText("\n".join(info_lines))
        copy_button = None
        if paypal_email:
            copy_button = dialog.addButton(
                _t(self.ui_language, "复制 PayPal 账号", "Copy PayPal Account"),
                QMessageBox.ActionRole,
            )
        dialog.addButton(_t(self.ui_language, "关闭", "Close"), QMessageBox.RejectRole)
        dialog.exec()

        if copy_button is not None and dialog.clickedButton() is copy_button:
            QApplication.clipboard().setText(paypal_email)
            copied_dialog = QMessageBox(self)
            copied_dialog.setIcon(QMessageBox.NoIcon)
            copied_dialog.setWindowTitle(title)
            copied_dialog.setText(
                _t(
                    self.ui_language,
                    f"PayPal 账号已复制到剪贴板：{paypal_email}",
                    f"PayPal account copied to clipboard: {paypal_email}",
                )
            )
            copied_dialog.addButton(_t(self.ui_language, "关闭", "Close"), QMessageBox.AcceptRole)
            copied_dialog.exec()

    def _open_ai_settings(self) -> None:
        previous_language = self.context.settings.get_ui_language()
        dialog = AISettingsDialog(self.context, ui_language=self.ui_language, parent=self)
        accepted = dialog.exec() == QDialog.Accepted
        if not accepted:
            return
        if self.on_ai_settings_changed:
            self.on_ai_settings_changed()
        latest_language = self.context.settings.get_ui_language()
        if latest_language != previous_language and self.on_ui_language_changed:
            self.on_ui_language_changed(latest_language)


class SettingsPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        layout.addWidget(
            make_page_title(
                "设置",
                "这里后面会放 OpenAI Key、默认模型、Excel 导出位置和本地运行参数。第一版先保留为设置入口。",
            )
        )

        card = make_card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(10)
        for bullet in (
            "OpenAI API 配置",
            "默认模型和速率限制",
            "Excel 导出路径",
            "日志和本地数据库位置",
        ):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            dot = QLabel("•")
            text = QLabel(bullet)
            row_layout.addWidget(dot)
            row_layout.addWidget(text, 1)
            card_layout.addWidget(row)
        layout.addWidget(card)
        layout.addStretch(1)

