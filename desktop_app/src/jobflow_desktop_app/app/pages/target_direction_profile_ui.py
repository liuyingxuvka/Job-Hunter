from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit

from ..widgets.common import _t


def find_profile(profile_records: list[Any], profile_id: int | None) -> Any | None:
    for profile in profile_records:
        if getattr(profile, "profile_id", None) == profile_id:
            return profile
    return None


def populate_direction_list(
    direction_list: QListWidget,
    profiles: list[Any],
    *,
    target_profile_id: int | None,
    prepare_profile: Callable[[Any], Any],
    display_role_name: Callable[[Any], str],
    display_scope_label: Callable[[Any], str],
    untitled_label: str,
) -> tuple[list[Any], int | None]:
    prepared_profiles: list[Any] = []
    target_row: int | None = None

    direction_list.blockSignals(True)
    direction_list.clear()
    for row_index, raw_profile in enumerate(profiles):
        profile = prepare_profile(raw_profile)
        prepared_profiles.append(profile)
        display_name = display_role_name(profile) or untitled_label
        scope_label = str(display_scope_label(profile) or "").strip()
        if scope_label:
            display_name = f"[{scope_label}] {display_name}"
        item = QListWidgetItem(display_name)
        item.setData(Qt.UserRole, getattr(profile, "profile_id", None))
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if getattr(profile, "is_active", False) else Qt.Unchecked)
        direction_list.addItem(item)
        if target_profile_id == getattr(profile, "profile_id", None):
            target_row = row_index
    direction_list.blockSignals(False)
    return prepared_profiles, target_row


def load_profile_form(
    ui_language: str,
    *,
    display_name: str,
    description_text: str,
    direction_name_input: QLineEdit,
    direction_reason_input: QPlainTextEdit,
    profile_meta_label: QLabel,
) -> None:
    direction_name_input.setText(display_name)
    direction_reason_input.setPlainText(description_text)
    profile_meta_label.setText(
        _t(
            ui_language,
            f"当前岗位 · {display_name}",
            f"Role · {display_name}",
        )
    )


def clear_profile_form(
    ui_language: str,
    *,
    has_candidate: bool,
    direction_name_input: QLineEdit,
    direction_reason_input: QPlainTextEdit,
    profile_meta_label: QLabel,
) -> None:
    direction_name_input.clear()
    direction_reason_input.clear()
    if has_candidate:
        profile_meta_label.setText(
            _t(
                ui_language,
                "暂无目标岗位，可用 AI 推荐或手动添加。",
                "No target roles yet. Use AI recommend or add one manually.",
            )
        )


__all__ = [
    "clear_profile_form",
    "find_profile",
    "load_profile_form",
    "populate_direction_list",
]
