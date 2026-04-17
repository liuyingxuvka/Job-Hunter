from __future__ import annotations

import os

from PySide6.QtCore import Qt

from ..widgets.common import _t


def populate_api_key_env_options(dialog, selected_env_var: str = "") -> None:
    selected = str(selected_env_var or "").strip()
    options = dialog.context.settings.list_api_key_environment_variables()
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

    dialog.api_key_env_combo.blockSignals(True)
    dialog.api_key_env_combo.clear()
    if ordered:
        dialog._has_env_var_options = True
        for name in ordered:
            dialog.api_key_env_combo.addItem(name, name)
        if selected:
            selected_upper = selected.upper()
            index = -1
            for offset in range(dialog.api_key_env_combo.count()):
                item_text = str(dialog.api_key_env_combo.itemText(offset) or "")
                if item_text.upper() == selected_upper:
                    index = offset
                    break
        else:
            index = -1
        if index >= 0:
            dialog.api_key_env_combo.setCurrentIndex(index)
        else:
            dialog.api_key_env_combo.setCurrentIndex(0)
        current_env = str(dialog.api_key_env_combo.currentData() or "").strip()
        if current_env:
            dialog._cached_api_key_env_var = current_env
    else:
        dialog._has_env_var_options = False
        dialog.api_key_env_combo.addItem(
            _t(
                dialog.ui_language,
                "未检测到可选环境变量（请先在系统中设置）",
                "No environment variable detected (set one in your system first).",
            ),
            "",
        )
        dialog.api_key_env_combo.setCurrentIndex(0)
    dialog.api_key_env_combo.blockSignals(False)


def set_env_combo_waiting_state(dialog) -> None:
    dialog._has_env_var_options = False
    dialog.api_key_env_combo.blockSignals(True)
    dialog.api_key_env_combo.clear()
    dialog.api_key_env_combo.addItem(
        _t(
            dialog.ui_language,
            "切换到“使用环境变量”后加载可选项",
            "Switch to 'Use Environment Variable' to load options",
        ),
        "",
    )
    dialog.api_key_env_combo.setCurrentIndex(0)
    dialog.api_key_env_combo.blockSignals(False)


def current_api_key_source(dialog) -> str:
    raw = str(dialog.api_key_source_combo.currentData() or dialog.API_KEY_SOURCE_DIRECT).strip().lower()
    if raw == dialog.API_KEY_SOURCE_ENV:
        return dialog.API_KEY_SOURCE_ENV
    return dialog.API_KEY_SOURCE_DIRECT


def current_api_key_env_var(dialog) -> str:
    if current_api_key_source(dialog) != dialog.API_KEY_SOURCE_ENV:
        return str(dialog._cached_api_key_env_var or "").strip()
    return str(dialog.api_key_env_combo.currentData() or "").strip()


def resolve_api_key_for_actions(dialog) -> tuple[str, str]:
    source = current_api_key_source(dialog)
    if source == dialog.API_KEY_SOURCE_DIRECT:
        key = dialog.api_key_input.text().strip()
        if key:
            return key, ""
        return (
            "",
            _t(
                dialog.ui_language,
                "当前是直接输入模式，但 API Key 为空。",
                "Direct mode is selected, but API key is empty.",
            ),
        )

    env_name = current_api_key_env_var(dialog)
    if not env_name:
        return (
            "",
            _t(
                dialog.ui_language,
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
            dialog.ui_language,
            f"环境变量 {env_name} 当前为空或不存在。",
            f"Environment variable {env_name} is empty or not set.",
        ),
    )


def on_api_credential_changed(dialog, _: str = "") -> None:
    if getattr(dialog, "_ui_loading", False):
        return
    dialog._detected_model_ids = []
    dialog._populate_model_options([], "")
    dialog._lock_model_selector(
        _t(
            dialog.ui_language,
            "API 凭据已变更。请点击“检测并加载模型”重新获取可用模型。",
            "API credentials changed. Click 'Detect & Load Models' to reload available models.",
        )
    )


def on_env_selection_changed(dialog, _: object = "") -> None:
    selected = str(dialog.api_key_env_combo.currentData() or "").strip()
    if selected:
        dialog._cached_api_key_env_var = selected
    dialog._on_api_credential_changed()


def on_key_source_changed(dialog) -> None:
    source = current_api_key_source(dialog)
    direct_mode = source == dialog.API_KEY_SOURCE_DIRECT
    if direct_mode:
        current_env = str(dialog.api_key_env_combo.currentData() or "").strip()
        if current_env:
            dialog._cached_api_key_env_var = current_env
        dialog._set_env_combo_waiting_state()
    else:
        dialog._populate_api_key_env_options(dialog._cached_api_key_env_var)
    dialog.api_key_input.setEnabled(direct_mode)
    dialog.api_key_env_combo.setEnabled((not direct_mode) and dialog._has_env_var_options)
    dialog.api_key_input.setStyleSheet(dialog.ACTIVE_INPUT_STYLE if direct_mode else dialog.INACTIVE_INPUT_STYLE)
    dialog.api_key_env_combo.setStyleSheet(dialog.INACTIVE_INPUT_STYLE if direct_mode else dialog.ACTIVE_INPUT_STYLE)
    if direct_mode:
        dialog.api_key_input.setPlaceholderText("sk-...")
        dialog.api_key_input.setFocus(Qt.OtherFocusReason)
    else:
        dialog.api_key_input.setPlaceholderText(
            _t(
                dialog.ui_language,
                "当前使用环境变量读取，不会使用这里的输入值。",
                "Environment-variable mode is active; this input is not used.",
            )
        )
        if dialog._has_env_var_options:
            dialog.api_key_env_combo.setFocus(Qt.OtherFocusReason)
    dialog._on_api_credential_changed()


__all__ = [
    "current_api_key_env_var",
    "current_api_key_source",
    "on_api_credential_changed",
    "on_env_selection_changed",
    "on_key_source_changed",
    "populate_api_key_env_options",
    "resolve_api_key_for_actions",
    "set_env_combo_waiting_state",
]
