from __future__ import annotations

from datetime import datetime
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QDialog,
    QAbstractItemView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..theme import UI_COLORS
from ..widgets.common import _t, make_card, styled_button
from ..widgets.dialog_presenter import QtDialogPresenter
from .search_results import SearchResultsStep
from . import search_results_row_rendering


class SearchResultsCompactStep(SearchResultsStep):
    """Compact search results UI backed by shared search behavior/state."""

    _COMPACT_VISUAL_ORDER = (0, 1, 7, 5, 8, 3, 4, 2, 6)
    _COMPACT_DEFAULT_WIDTHS = {
        0: 42,   # delete checkbox
        1: 220,  # title
        7: 80,   # score
        5: 140,  # link
        8: 124,  # status
        3: 130,  # company
        4: 112,  # location
        2: 180,  # target role
        6: 104,  # found time
    }

    def __init__(self, context, ui_language: str = "zh") -> None:
        self._delete_checked_job_keys: set[str] = set()
        self._restore_checked_job_keys: set[str] = set()
        self._compact_default_widths_applied = False
        self.dialogs = QtDialogPresenter()
        super().__init__(context, ui_language=ui_language)
        self._rebuild_compact_layout()
        QTimer.singleShot(0, self._apply_compact_default_widths)

    def _rebuild_compact_layout(self) -> None:
        root_layout = self.layout()
        if root_layout is None:
            raise RuntimeError("SearchResultsStep root layout is missing.")
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(10)

        root_layout.addWidget(self._build_compact_control_card())
        root_layout.addWidget(self._build_compact_results_card(), 1)

    def _build_compact_control_card(self) -> QFrame:
        card = make_card()
        card.setObjectName("CompactResultsControlCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        toolbar = QWidget()
        toolbar.setObjectName("CompactResultsToolbar")
        toolbar.setProperty("transparentBg", True)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(7)

        toolbar_layout.addWidget(self.refresh_button)
        self.delete_button.setText(_t(self.ui_language, "删除勾选岗位", "Delete Checked Jobs"))
        toolbar_layout.addWidget(self.delete_button)

        duration_group = QWidget()
        duration_group.setProperty("transparentBg", True)
        duration_layout = QHBoxLayout(duration_group)
        duration_layout.setContentsMargins(6, 0, 0, 0)
        duration_layout.setSpacing(7)
        duration_layout.addWidget(self.search_duration_label)
        duration_layout.addWidget(self.search_duration_combo)
        duration_layout.addWidget(self.search_countdown_label)
        duration_layout.addWidget(self.search_countdown_value_label)

        toolbar_layout.addWidget(duration_group)
        toolbar_layout.addStretch(1)
        self.recycle_bin_button = styled_button(_t(self.ui_language, "回收站", "Recycle Bin"))
        self.recycle_bin_button.clicked.connect(self._open_recycle_bin_dialog)
        toolbar_layout.addWidget(self.recycle_bin_button)
        layout.addWidget(toolbar)

        self.results_meta_label.setWordWrap(False)
        self.results_meta_label.setObjectName("InlineStatusLabel")
        self.results_progress_label.setWordWrap(False)
        self.results_progress_label.setObjectName("InlineMetaLabel")
        layout.addWidget(self.results_progress_label)
        self.results_stats_label.setWordWrap(False)
        self.results_stats_label.setObjectName("InlineMetaLabel")
        status_row = QWidget()
        status_row.setObjectName("CompactResultsStatusRow")
        status_row.setProperty("transparentBg", True)
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(12)
        status_layout.addWidget(self.results_meta_label)
        status_layout.addWidget(self.results_stats_label)
        status_layout.addStretch(1)
        layout.addWidget(status_row)
        return card

    def _build_compact_results_card(self) -> QFrame:
        card = make_card()
        card.setObjectName("CompactResultsTableCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)

        self.table.setObjectName("CompactResultsTable")
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            [
                "",
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
        self.table.setMinimumHeight(420)
        self.table.setAlternatingRowColors(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setMinimumWidth(34)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.verticalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.table.setStyleSheet(
            f"""
            QTableWidget#CompactResultsTable {{
              background: {UI_COLORS["bg_card"]};
              alternate-background-color: {UI_COLORS["bg_card"]};
              selection-background-color: {UI_COLORS["selection_bg"]};
              selection-color: {UI_COLORS["text_heading"]};
              gridline-color: #e3e8ef;
              border: 1px solid {UI_COLORS["border"]};
              border-radius: 12px;
              padding: 0px;
            }}
            QTableWidget#CompactResultsTable QTableView {{
              background: {UI_COLORS["bg_card"]};
            }}
            QTableWidget#CompactResultsTable::item {{
              background: {UI_COLORS["bg_card"]};
              color: {UI_COLORS["text_primary"]};
            }}
            QTableWidget#CompactResultsTable QHeaderView::section {{
              background: {UI_COLORS["bg_card"]};
              color: {UI_COLORS["text_heading"]};
              border: none;
              border-right: 1px solid #e3e8ef;
              border-bottom: 1px solid #e3e8ef;
              padding: 8px;
              font-weight: 600;
            }}
            QTableWidget#CompactResultsTable QTableCornerButton::section {{
              background: {UI_COLORS["bg_card"]};
              border: none;
              border-right: 1px solid #e3e8ef;
              border-bottom: 1px solid #e3e8ef;
            }}
            """
        )
        self.table.verticalHeader().setStyleSheet(
            f"""
            QHeaderView {{
              background: {UI_COLORS["bg_card"]};
            }}
            QHeaderView::section {{
              background: {UI_COLORS["bg_card"]};
              color: {UI_COLORS["text_heading"]};
              border: none;
              border-right: 1px solid #e3e8ef;
              border-bottom: 1px solid #e3e8ef;
              padding: 0px 6px;
            }}
            """
        )
        self.table.horizontalHeader().viewport().setStyleSheet(
            f"background: {UI_COLORS['bg_card']};"
        )
        self.table.verticalHeader().viewport().setStyleSheet(
            f"background: {UI_COLORS['bg_card']};"
        )
        self._apply_compact_table_headers()
        self._apply_compact_visual_order()
        layout.addWidget(self.table, 1)
        return card

    def _render_visible_jobs(self, visible_jobs) -> int:
        self.table.setRowCount(0)
        self._populate_compact_table(
            self.table,
            visible_jobs,
            checkbox_cell_factory=self._make_delete_checkbox_cell,
        )
        rendered = len(visible_jobs)
        self._apply_compact_visual_order()
        self._apply_compact_display_overrides()
        return rendered

    def _make_delete_checkbox_cell(self, job_key: str) -> QWidget:
        return self._make_checkbox_cell(
            job_key=job_key,
            checked_keys=self._delete_checked_job_keys,
            toggled_handler=self._on_delete_checkbox_toggled,
        )

    def _make_restore_checkbox_cell(self, job_key: str) -> QWidget:
        return self._make_checkbox_cell(
            job_key=job_key,
            checked_keys=self._restore_checked_job_keys,
            toggled_handler=self._on_restore_checkbox_toggled,
        )

    def _make_checkbox_cell(
        self,
        *,
        job_key: str,
        checked_keys: set[str],
        toggled_handler,
    ) -> QWidget:
        container = QWidget()
        container.setProperty("transparentBg", True)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        checkbox = QPushButton(container)
        checkbox.setCheckable(True)
        checkbox.setProperty("jobKey", job_key)
        checkbox.setFocusPolicy(Qt.NoFocus)
        checkbox.setChecked(job_key in checked_keys)
        checkbox.setFixedSize(20, 20)
        checkbox.setText("✓" if checkbox.isChecked() else "")
        checkbox.setCursor(Qt.PointingHandCursor)
        checkbox.setStyleSheet(
            f"""
            QPushButton {{
              background: transparent;
              border: 2px solid {UI_COLORS["border_muted"]};
              border-radius: 4px;
              color: {UI_COLORS["text_primary"]};
              font-size: 15px;
              font-weight: 700;
              min-width: 16px;
              max-width: 16px;
              min-height: 16px;
              max-height: 16px;
              padding: 0px;
            }}
            QPushButton:hover {{
              border-color: {UI_COLORS["text_muted"]};
            }}
            QPushButton:checked {{
              background: {UI_COLORS["bg_card"]};
              border-color: {UI_COLORS["text_heading"]};
              color: {UI_COLORS["text_primary"]};
            }}
            """
        )
        checkbox.toggled.connect(
            lambda checked, button=checkbox, key=job_key: toggled_handler(
                key,
                button,
                checked,
            )
        )
        layout.addWidget(checkbox, 0, Qt.AlignCenter)
        return container

    def _populate_compact_table(self, table: QTableWidget, jobs, *, checkbox_cell_factory) -> None:
        table.setRowCount(0)
        for row_index, job in enumerate(jobs):
            table.insertRow(row_index)
            job_key = self._job_key(job)
            detail_url, _final_url, _link_status = self._job_link_details(job)

            table.setCellWidget(row_index, 0, checkbox_cell_factory(job_key))

            title_item = self._make_centered_item(
                self._display_job_title(job),
                job_key=job_key,
            )
            table.setItem(row_index, 1, title_item)
            table.setItem(row_index, 2, self._make_centered_item(self._display_target_role(job)))
            table.setItem(row_index, 3, self._make_centered_item(str(getattr(job, "company", "") or "")))
            table.setItem(row_index, 4, self._make_centered_item(self._display_job_location(job)))
            table.setCellWidget(row_index, 5, self._make_link_cell(detail_url, False))
            table.setItem(row_index, 6, self._make_centered_item(str(getattr(job, "date_found", "") or "")))
            table.setItem(row_index, 7, self._make_centered_item(self._format_score(job)))
            table.setCellWidget(
                row_index,
                8,
                search_results_row_rendering.build_status_combo(
                    job_key=job_key,
                    status_codes=self.STATUS_CODES,
                    status_by_job_key=self.status_by_job_key,
                    status_display=self._status_display,
                    decorate_status_combo_items=self._decorate_status_combo_items,
                    apply_status_combo_style=self._apply_status_combo_style,
                    on_status_changed=self._on_status_changed,
                ),
            )

    def _make_centered_item(self, text: str, *, job_key: str = "") -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        if job_key:
            item.setData(Qt.UserRole, job_key)
        return item

    def _apply_compact_table_headers(self) -> None:
        self._apply_compact_table_headers_to(self.table)

    def _apply_compact_table_headers_to(self, table: QTableWidget) -> None:
        table.horizontalHeaderItem(5).setText(_t(self.ui_language, "详情链接", "Details"))
        table.horizontalHeaderItem(7).setText(_t(self.ui_language, "评分", "Score"))

    def _apply_compact_visual_order(self) -> None:
        self._apply_compact_visual_order_to(self.table)

    def _apply_compact_visual_order_to(self, table: QTableWidget) -> None:
        header = table.horizontalHeader()
        header.setSectionsMovable(True)
        for visual_index, logical_index in enumerate(self._COMPACT_VISUAL_ORDER):
            current_visual_index = header.visualIndex(logical_index)
            if current_visual_index != visual_index:
                header.moveSection(current_visual_index, visual_index)

    def _apply_compact_default_widths(self) -> None:
        if self._compact_default_widths_applied:
            return
        self._apply_compact_default_widths_to(self.table)
        self._compact_default_widths_applied = True

    def _apply_compact_default_widths_to(self, table: QTableWidget) -> None:
        for logical_index, width in self._COMPACT_DEFAULT_WIDTHS.items():
            table.setColumnWidth(logical_index, width)

    def _apply_compact_display_overrides(self) -> None:
        self._apply_compact_display_overrides_to(self.table)

    def _apply_compact_display_overrides_to(self, table: QTableWidget) -> None:
        for row in range(table.rowCount()):
            self._compress_table_item(table, row, 1)
            self._compress_table_item(table, row, 2)
            self._compress_table_item(table, row, 3)
            self._compress_table_item(table, row, 4)
            self._compress_found_time_item(table, row, 6)

    def _compress_table_item(self, table: QTableWidget, row: int, column: int) -> None:
        item = table.item(row, column)
        if item is None:
            return
        text = item.text().strip()
        if text:
            item.setToolTip(text)

    def _compress_found_time_item(self, table: QTableWidget, row: int, column: int) -> None:
        item = table.item(row, column)
        if item is None:
            return
        full_text = item.text().strip()
        short_text = self._compact_found_time_text(full_text)
        item.setText(short_text)
        item.setToolTip(full_text or short_text)

    def _compact_found_time_text(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return "-"
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed.strftime("%m-%d %H:%M")
        except ValueError:
            compact = text.replace("T", " ")
            return compact[:16] if len(compact) > 16 else compact

    def _make_link_cell(self, url: str, prefer_detail_label: bool) -> QLabel:
        # Compact table keeps link visibility early in the row with a shorter label.
        return super()._make_link_cell(url, False)

    def _format_score(self, job) -> str:
        score = getattr(job, "bound_target_role_score", None)
        if score is None:
            score = getattr(job, "match_score", None)
        if score is None:
            return _t(self.ui_language, "无", "NA")
        if score >= 85:
            level = _t(self.ui_language, "高", "High")
        elif score >= 70:
            level = _t(self.ui_language, "中", "Med")
        else:
            level = _t(self.ui_language, "低", "Low")
        return f"{score} {level}"

    def _set_results_main_status(self, text: str) -> None:
        message = self._compact_status_line(text)
        if self.results_meta_label.text() != message:
            self.results_meta_label.setText(message)

    def _set_compact_label_tone(self, label: QLabel, object_name: str) -> None:
        if label.objectName() == object_name:
            return
        label.setObjectName(object_name)
        QApplication.style().unpolish(label)
        QApplication.style().polish(label)
        label.update()

    def _set_results_progress_detail_with_level(self, text: str, *, alert: bool) -> None:
        message = self._compact_progress_line(text)
        if alert and message:
            self._set_compact_label_tone(self.results_progress_label, "InlineErrorLabel")
        else:
            self._set_compact_label_tone(self.results_progress_label, "InlineMetaLabel")
        if self.results_progress_label.text() != message:
            self.results_progress_label.setText(message)
        self.results_progress_label.setVisible(bool(message))

    def _refresh_results_stats_label(self) -> None:
        candidate_id = self.current_candidate_id
        if candidate_id is None:
            text = _t(
                self.ui_language,
                "未选择求职者",
                "No candidate selected",
            )
        else:
            try:
                stats = self.runner.load_search_stats(candidate_id)
            except Exception as exc:
                text = _t(
                    self.ui_language,
                    f"统计读取失败：{exc}",
                    f"Stats unavailable: {exc}",
                )
            else:
                text = self._compact_stats_line(stats)
        if self.results_stats_label.text() != text:
            self.results_stats_label.setText(text)

    def _set_loaded_results_status(self, visible_count: int, pending_count: int) -> None:
        if visible_count > 0 and pending_count > 0:
            message = _t(
                self.ui_language,
                "已加载最近结果，待补完岗位下次会优先继续",
                "Loaded latest results; pending jobs will resume first next time",
            )
        elif visible_count > 0:
            message = _t(
                self.ui_language,
                "已加载最近结果",
                "Loaded latest results",
            )
        elif pending_count > 0:
            message = _t(
                self.ui_language,
                "暂无结果，待补完岗位下次会优先继续",
                "No results yet; pending jobs will resume first next time",
            )
        else:
            message = _t(
                self.ui_language,
                "等待开始搜索",
                "Ready to start",
            )
        self._set_results_main_status(message)
        self._set_results_progress_detail("")

    def _compact_status_line(self, text: str) -> str:
        raw = self._normalize_status_text(text)
        if not raw:
            return _t(self.ui_language, "等待开始搜索", "Ready to start")
        mappings = (
            ("已加载最近一次运行结果", _t(self.ui_language, "已加载最近结果", "Loaded latest results")),
            ("点击“开始搜索”后", _t(self.ui_language, "等待开始搜索", "Ready to start")),
            ("正在后台搜索岗位", _t(self.ui_language, "后台搜索中", "Search running")),
            ("已请求停止", _t(self.ui_language, "正在停止当前搜索", "Stopping current search")),
            ("下一轮搜索已排队", _t(self.ui_language, "下一轮已排队", "Next round queued")),
            ("请先返回选择页", _t(self.ui_language, "请先选择求职者", "Select a candidate first")),
            ("No candidate is selected", _t(self.ui_language, "请先选择求职者", "Select a candidate first")),
        )
        for needle, replacement in mappings:
            if needle in raw:
                return replacement
        return self._first_sentence(raw, limit=52)

    def _compact_progress_line(self, text: str) -> str:
        raw = self._normalize_status_text(text)
        if not raw:
            return ""
        if raw.startswith("本轮找到 ") or raw.startswith("This round found "):
            return ""
        if raw.startswith("最近公司诊断：") or raw.startswith("Latest company diagnosis:"):
            return ""
        mappings = (
            ("后台进度：", ""),
            ("后台收尾：", ""),
            ("已删除的岗位可在回收站中恢复。", _t(self.ui_language, "已删除岗位可在回收站恢复", "Deleted jobs can be restored from recycle bin")),
        )
        for needle, replacement in mappings:
            if raw.startswith(needle):
                suffix = raw[len(needle):].strip()
                return (replacement + suffix).strip()
        return self._first_sentence(raw, limit=60)

    def _compact_stats_line(self, stats) -> str:
        return _t(
            self.ui_language,
            (
                f"公司池 {max(0, int(getattr(stats, 'candidate_company_pool_count', 0) or 0))} · "
                f"发现 {max(0, int(getattr(stats, 'main_discovered_job_count', 0) or 0))} · "
                f"分析 {max(0, int(getattr(stats, 'main_scored_job_count', 0) or 0))} · "
                f"推荐 {max(0, int(getattr(stats, 'recommended_job_count', 0) or 0))} · "
                f"待补完 {max(0, int(getattr(stats, 'main_pending_analysis_count', 0) or 0))}"
            ),
            (
                f"Pool {max(0, int(getattr(stats, 'candidate_company_pool_count', 0) or 0))} · "
                f"found {max(0, int(getattr(stats, 'main_discovered_job_count', 0) or 0))} · "
                f"scored {max(0, int(getattr(stats, 'main_scored_job_count', 0) or 0))} · "
                f"recommended {max(0, int(getattr(stats, 'recommended_job_count', 0) or 0))} · "
                f"pending {max(0, int(getattr(stats, 'main_pending_analysis_count', 0) or 0))}"
            ),
        )

    def _normalize_status_text(self, text: str) -> str:
        raw = str(text or "").strip().replace("\n", " ")
        raw = re.sub(r"\s+", " ", raw)
        if self.ui_language == "zh":
            raw = re.sub(r"^当前求职者：[^。]*。\s*", "", raw)
        else:
            raw = re.sub(r"^Current candidate:[^.]*\.\s*", "", raw)
        return raw.strip()

    def _first_sentence(self, text: str, *, limit: int) -> str:
        raw = str(text or "").strip()
        for separator in ("。", ". ", "\n"):
            if separator in raw:
                raw = raw.split(separator, 1)[0].strip()
                break
        if len(raw) > limit:
            return raw[: limit - 1].rstrip() + "…"
        return raw

    def _on_delete_checkbox_toggled(self, job_key: str, button: QPushButton, checked: bool) -> None:
        if not job_key:
            return
        button.setText("✓" if checked else "")
        if checked:
            self._delete_checked_job_keys.add(job_key)
        else:
            self._delete_checked_job_keys.discard(job_key)

    def _checked_delete_job_keys(self) -> list[str]:
        keys: list[str] = []
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            if widget is None:
                continue
            checkbox = widget.findChild(QPushButton)
            if checkbox is None or not checkbox.isChecked():
                continue
            job_key = str(checkbox.property("jobKey") or "").strip()
            if job_key:
                keys.append(job_key)
        return keys

    def _checked_restore_job_keys(self, table: QTableWidget) -> list[str]:
        keys: list[str] = []
        for row in range(table.rowCount()):
            widget = table.cellWidget(row, 0)
            if widget is None:
                continue
            checkbox = widget.findChild(QPushButton)
            if checkbox is None or not checkbox.isChecked():
                continue
            job_key = str(checkbox.property("jobKey") or "").strip()
            if job_key:
                keys.append(job_key)
        return keys

    def _delete_selected_rows(self) -> None:
        job_keys = self._checked_delete_job_keys()
        if not job_keys:
            self.dialogs.information(
                self,
                _t(self.ui_language, "岗位搜索结果", "Search Results"),
                _t(
                    self.ui_language,
                    "请先勾选要删除的岗位。",
                    "Please check the job rows you want to delete first.",
                ),
            )
            return
        confirm = self.dialogs.confirm(
            self,
            _t(self.ui_language, "确认删除", "Confirm Deletion"),
            _t(
                self.ui_language,
                f"确定要删除这 {len(job_keys)} 条勾选岗位吗？这些岗位会进入回收站，可稍后恢复。",
                f"Delete these {len(job_keys)} checked job(s)? They will move to the recycle bin and can be restored later.",
            ),
        )
        if not confirm:
            return

        for job_key in job_keys:
            self.hidden_job_keys.add(job_key)
            self._delete_checked_job_keys.discard(job_key)
        self._save_review_state()
        self._live_results_signature = ()

        candidate_id = self.current_candidate_id
        if candidate_id is not None:
            if self._is_search_running(candidate_id):
                self._refresh_live_results()
                self._show_notification_toast(
                    _t(
                        self.ui_language,
                        f"已删除 {len(job_keys)} 条岗位，已进入回收站。",
                        f"Deleted {len(job_keys)} job(s) to the recycle bin.",
                    ),
                    "info",
                    3000,
                )
            else:
                self._reload_existing_results(candidate_id)
                self._set_deleted_status(len(job_keys))
                self._set_results_progress_detail(
                    _t(
                        self.ui_language,
                        "已删除的岗位可在回收站中恢复。",
                        "Deleted jobs can be restored from the recycle bin.",
                    )
                )

    def _deleted_jobs_for_recycle_bin(self) -> list[dict[str, str]]:
        candidate_id = self.current_candidate_id
        if candidate_id is None or not self.hidden_job_keys:
            return []
        jobs = self.runner.load_live_jobs(candidate_id)
        if not jobs:
            jobs = self.runner.load_recommended_jobs(candidate_id)
        deleted_jobs: list[object] = []
        for job in jobs:
            job_key = self._job_key(job)
            if job_key not in self.hidden_job_keys:
                continue
            deleted_jobs.append(job)
        deleted_jobs.sort(
            key=lambda item: str(getattr(item, "date_found", "") or ""),
            reverse=True,
        )
        return deleted_jobs

    def _build_recycle_bin_dialog(self, deleted_jobs) -> tuple[QDialog, QTableWidget, QPushButton, QPushButton]:
        self._restore_checked_job_keys.clear()
        dialog = QDialog(self)
        dialog.setWindowTitle(_t(self.ui_language, "回收站", "Recycle Bin"))
        dialog.resize(1220, 560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        intro = QLabel(
            _t(
                self.ui_language,
                "已删除岗位可在这里恢复。",
                "Restore deleted jobs here.",
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        table = QTableWidget(len(deleted_jobs), 9, dialog)
        table.setSelectionBehavior(self.table.selectionBehavior())
        table.setSelectionMode(self.table.selectionMode())
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(False)
        table.setWordWrap(False)
        table.setObjectName("CompactResultsTable")
        table.setMinimumHeight(420)
        table.verticalHeader().setMinimumWidth(34)
        table.verticalHeader().setDefaultSectionSize(44)
        table.verticalHeader().setDefaultAlignment(Qt.AlignCenter)
        table.setStyleSheet(self.table.styleSheet())
        table.verticalHeader().setStyleSheet(self.table.verticalHeader().styleSheet())
        table.verticalHeader().viewport().setStyleSheet("background: #ffffff;")
        table.setHorizontalHeaderLabels(
            [
                "",
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
        self._populate_compact_table(
            table,
            deleted_jobs,
            checkbox_cell_factory=self._make_restore_checkbox_cell,
        )
        self._apply_compact_table_headers_to(table)
        self._apply_compact_visual_order_to(table)
        self._apply_compact_default_widths_to(table)
        self._apply_compact_display_overrides_to(table)
        layout.addWidget(table, 1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        restore_selected_button = styled_button(_t(self.ui_language, "恢复勾选岗位", "Restore Checked"))
        restore_all_button = styled_button(_t(self.ui_language, "恢复全部", "Restore All"))
        close_button = styled_button(_t(self.ui_language, "关闭", "Close"))
        buttons.addWidget(restore_selected_button)
        buttons.addWidget(restore_all_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        restore_selected_button.clicked.connect(
            lambda: self._restore_hidden_job_keys(sorted(self._restore_checked_job_keys), dialog)
        )
        restore_all_button.clicked.connect(
            lambda: self._restore_hidden_job_keys([self._job_key(item) for item in deleted_jobs], dialog)
        )
        close_button.clicked.connect(dialog.reject)
        return dialog, table, restore_selected_button, restore_all_button

    def _open_recycle_bin_dialog(self) -> None:
        deleted_jobs = self._deleted_jobs_for_recycle_bin()
        if not deleted_jobs:
            self.dialogs.information(
                self,
                _t(self.ui_language, "回收站", "Recycle Bin"),
                _t(
                    self.ui_language,
                    "当前回收站里没有岗位。",
                    "There are no jobs in the recycle bin right now.",
                ),
            )
            return
        dialog, table, restore_selected_button, restore_all_button = self._build_recycle_bin_dialog(deleted_jobs)
        dialog.exec()

    def _on_restore_checkbox_toggled(self, job_key: str, button: QPushButton, checked: bool) -> None:
        button.setText("✓" if checked else "")
        if checked:
            self._restore_checked_job_keys.add(job_key)
        else:
            self._restore_checked_job_keys.discard(job_key)

    def _restore_hidden_job_keys(self, job_keys: list[str], dialog: QDialog | None = None) -> None:
        if not job_keys:
            return
        for job_key in job_keys:
            self.hidden_job_keys.discard(job_key)
            self._delete_checked_job_keys.discard(job_key)
            self._restore_checked_job_keys.discard(job_key)
        self._save_review_state()
        self._live_results_signature = ()
        candidate_id = self.current_candidate_id
        if candidate_id is not None:
            if self._is_search_running(candidate_id):
                self._refresh_live_results()
            else:
                self._reload_existing_results(candidate_id)
        if dialog is not None:
            dialog.accept()
