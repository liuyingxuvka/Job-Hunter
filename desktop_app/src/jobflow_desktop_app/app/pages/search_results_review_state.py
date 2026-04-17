from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QMessageBox, QTableWidget, QWidget

from ..widgets.common import _t
from . import search_results_review_store


def status_store_key(candidate_id: int | None) -> str:
    return search_results_review_store.status_store_key(candidate_id)


def hidden_store_key(candidate_id: int | None) -> str:
    return search_results_review_store.hidden_store_key(candidate_id)


def load_review_state(settings, candidate_id: int, normalize_status_code: Callable[[str], str | None]):
    return search_results_review_store.load_review_state(settings, candidate_id, normalize_status_code)


def save_review_state(
    settings,
    candidate_id: int | None,
    status_by_job_key: dict[str, str],
    hidden_job_keys: set[str],
) -> None:
    search_results_review_store.save_review_state(
        settings,
        candidate_id,
        status_by_job_key,
        hidden_job_keys,
    )


def apply_status_change(
    *,
    status_by_job_key: dict[str, str],
    job_key: str,
    status_text: str,
    normalize_status_code: Callable[[str], str | None],
    apply_status_style: Callable[[QComboBox, str], None],
    save_review_state: Callable[[], None],
    combo: QComboBox | None = None,
) -> None:
    status_code = normalize_status_code(status_text)
    if status_code is None:
        return
    status_by_job_key[job_key] = status_code
    if combo is not None:
        apply_status_style(combo, status_code)
    save_review_state()


def delete_selected_rows(
    *,
    owner: QWidget,
    ui_language: str,
    table: QTableWidget,
    hidden_job_keys: set[str],
    status_by_job_key: dict[str, str],
    save_review_state: Callable[[], None],
    sync_live_results_signature: Callable[[], None],
    is_search_running: Callable[[], bool],
    show_notification_toast: Callable[[str, str, int], None],
    set_deleted_status: Callable[[int], None],
) -> int:
    selected_rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
    if not selected_rows and table.currentRow() >= 0:
        selected_rows = [table.currentRow()]
    if not selected_rows:
        QMessageBox.information(
            owner,
            _t(ui_language, "岗位搜索结果", "Search Results"),
            _t(ui_language, "请先选中要删除的岗位行。", "Please select row(s) to delete first."),
        )
        return 0

    for row in selected_rows:
        key_item = table.item(row, 0)
        job_key = str(key_item.data(Qt.UserRole) or "").strip() if key_item is not None else ""
        if job_key:
            hidden_job_keys.add(job_key)
            status_by_job_key.pop(job_key, None)
        table.removeRow(row)

    save_review_state()
    sync_live_results_signature()
    deleted_count = len(selected_rows)
    if is_search_running():
        show_notification_toast(
            _t(
                ui_language,
                f"已删除 {deleted_count} 条岗位。",
                f"Deleted {deleted_count} row(s).",
            ),
            "info",
            3000,
        )
    else:
        set_deleted_status(deleted_count)
    return deleted_count


__all__ = [
    "apply_status_change",
    "delete_selected_rows",
    "hidden_store_key",
    "load_review_state",
    "save_review_state",
    "status_store_key",
]
