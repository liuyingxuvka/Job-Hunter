from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QTableWidget, QTableWidgetItem, QWidget


def build_status_combo(
    *,
    job_key: str,
    status_codes: tuple[str, ...],
    status_by_job_key: dict[str, str],
    status_display: Callable[[str], str],
    decorate_status_combo_items: Callable[[QComboBox], None],
    apply_status_combo_style: Callable[[QComboBox, str], None],
    on_status_changed: Callable[[str, str, QComboBox | None], None],
) -> QComboBox:
    status_combo = QComboBox()
    status_labels = [status_display(code) for code in status_codes]
    status_combo.addItems(status_labels)
    decorate_status_combo_items(status_combo)
    current_status_code = status_by_job_key.get(job_key, "pending")
    current_index = status_codes.index(current_status_code) if current_status_code in status_codes else 0
    status_combo.setCurrentIndex(current_index)
    apply_status_combo_style(status_combo, current_status_code)
    status_combo.currentTextChanged.connect(
        lambda text, key=job_key, combo=status_combo: on_status_changed(key, text, combo)
    )
    return status_combo


def populate_job_row(
    *,
    table: QTableWidget,
    row_index: int,
    job: Any,
    job_key: str,
    detail_url: str,
    status_codes: tuple[str, ...],
    status_by_job_key: dict[str, str],
    display_target_role: Callable[[Any], str],
    format_score: Callable[[Any], str],
    make_link_cell: Callable[[str, bool], QWidget],
    status_display: Callable[[str], str],
    decorate_status_combo_items: Callable[[QComboBox], None],
    apply_status_combo_style: Callable[[QComboBox, str], None],
    on_status_changed: Callable[[str, str, QComboBox | None], None],
) -> None:
    table.insertRow(row_index)
    title_item = QTableWidgetItem(str(getattr(job, "title", "") or ""))
    title_item.setData(Qt.UserRole, job_key)
    table.setItem(row_index, 0, title_item)
    table.setItem(row_index, 1, QTableWidgetItem(display_target_role(job)))
    table.setItem(row_index, 2, QTableWidgetItem(str(getattr(job, "company", "") or "")))
    table.setItem(row_index, 3, QTableWidgetItem(str(getattr(job, "location", "") or "")))
    table.setCellWidget(row_index, 4, make_link_cell(detail_url, True))
    table.setItem(row_index, 5, QTableWidgetItem(str(getattr(job, "date_found", "") or "")))
    table.setItem(row_index, 6, QTableWidgetItem(format_score(job)))
    table.setCellWidget(
        row_index,
        7,
        build_status_combo(
            job_key=job_key,
            status_codes=status_codes,
            status_by_job_key=status_by_job_key,
            status_display=status_display,
            decorate_status_combo_items=decorate_status_combo_items,
            apply_status_combo_style=apply_status_combo_style,
            on_status_changed=on_status_changed,
        ),
    )


__all__ = [
    "build_status_combo",
    "populate_job_row",
]
