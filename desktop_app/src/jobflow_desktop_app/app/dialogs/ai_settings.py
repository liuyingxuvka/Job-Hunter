from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ...db.repositories.settings import OpenAISettings
from ..context import AppContext
from ...ai.model_catalog import fetch_available_models
from ..widgets.common import _t, styled_button
from ..widgets.async_tasks import run_busy_task
from . import ai_settings_api_key_source

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
        self.setWindowTitle(_t(self.ui_language, "设置 / Settings", "Settings / 设置"))
        self.resize(720, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        title = QLabel(_t(self.ui_language, "设置 / Settings", "Settings / 设置"))
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
                _t(self.ui_language, "设置 / Settings", "Settings / 设置"),
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
                _t(self.ui_language, "设置 / Settings", "Settings / 设置"),
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
                _t(self.ui_language, "设置 / Settings", "Settings / 设置"),
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
            _t(self.ui_language, "设置 / Settings", "Settings / 设置"),
            message,
        )
        self.accept()

    def _populate_api_key_env_options(self, selected_env_var: str = "") -> None:
        ai_settings_api_key_source.populate_api_key_env_options(self, selected_env_var)

    def _set_env_combo_waiting_state(self) -> None:
        ai_settings_api_key_source.set_env_combo_waiting_state(self)

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
        ai_settings_api_key_source.on_api_credential_changed(self, _)

    def _on_env_selection_changed(self, _: object = "") -> None:
        ai_settings_api_key_source.on_env_selection_changed(self, _)

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
        return ai_settings_api_key_source.current_api_key_source(self)

    def _current_api_key_env_var(self) -> str:
        return ai_settings_api_key_source.current_api_key_env_var(self)

    def _resolve_api_key_for_actions(self) -> tuple[str, str]:
        return ai_settings_api_key_source.resolve_api_key_for_actions(self)

    def _on_key_source_changed(self) -> None:
        ai_settings_api_key_source.on_key_source_changed(self)

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
                    _t(self.ui_language, "设置 / Settings", "Settings / 设置"),
                    api_key_error,
                )
            return

        current_model = self.model_input.currentText().strip()
        base_url = self.context.settings.get_openai_base_url()
        stored_model = self.context.settings.get_openai_settings().model.strip()
        self.refresh_models_button.setEnabled(False)
        request_title = _t(self.ui_language, "设置 / Settings", "Settings / 设置")
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

