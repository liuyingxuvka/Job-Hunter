from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
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
from ..context import AppContext
from ...search.orchestration import JobSearchResult, JobSearchRunner
from ...common.location_codec import (
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
from ...ai.model_catalog import fetch_available_models, filter_response_usable_models
from ...ai.role_recommendations import (
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
from ..widgets.common import _t, make_card, make_page_title, make_scroll_area, styled_button
from ..widgets.async_tasks import run_busy_task

class CandidateDirectoryPage(QWidget):
    def __init__(
        self,
        context: AppContext,
        ui_language: str = "zh",
        on_data_changed: Callable[[], None] | None = None,
        on_candidate_selected: Callable[[int | None], None] | None = None,
        on_open_workspace: Callable[[int], None] | None = None,
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

    def selected_candidate_id(self) -> int | None:
        return self._selected_candidate_id_from_list()

    @property
    def current_candidate_id(self) -> int | None:
        return self.selected_candidate_id()

    @current_candidate_id.setter
    def current_candidate_id(self, candidate_id: int | None) -> None:
        self.set_selected_candidate_id(candidate_id, emit_selection=False)

    def set_selected_candidate_id(
        self,
        candidate_id: int | None,
        *,
        emit_selection: bool = True,
    ) -> None:
        target_row = None
        for row_index, record in enumerate(self.records):
            if record.candidate_id == candidate_id:
                target_row = row_index
                break
        self.candidate_list.blockSignals(True)
        if target_row is None:
            self.candidate_list.clearSelection()
            self.candidate_list.setCurrentRow(-1)
        else:
            self.candidate_list.setCurrentRow(target_row)
        self.candidate_list.blockSignals(False)
        self._update_action_state()
        if emit_selection and self.on_candidate_selected:
            self.on_candidate_selected(self.selected_candidate_id())

    def reload(self, select_candidate_id: int | None = None, *, emit_selection: bool = True) -> None:
        self.records = self.context.candidates.list_records()
        self.summaries_by_id = {
            summary.candidate_id: summary for summary in self.context.candidates.list_summaries()
        }
        preserve_id = select_candidate_id if select_candidate_id is not None else self.selected_candidate_id()

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
            self.set_selected_candidate_id(
                self.records[target_row].candidate_id,
                emit_selection=emit_selection,
            )
            return

        if self.records:
            self.set_selected_candidate_id(
                self.records[0].candidate_id,
                emit_selection=emit_selection,
            )
            return

        self.set_selected_candidate_id(None, emit_selection=emit_selection)

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
            self._update_action_state()
            if self.on_candidate_selected:
                self.on_candidate_selected(None)
            return

        candidate_id = self.selected_candidate_id()
        self._update_action_state()
        if self.on_candidate_selected:
            self.on_candidate_selected(candidate_id)

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

        self.reload(select_candidate_id=candidate_id, emit_selection=False)
        if self.on_candidate_selected:
            self.on_candidate_selected(candidate_id)
        if self.on_data_changed:
            self.on_data_changed()

    def _delete_candidate(self) -> None:
        candidate_id = self.selected_candidate_id()
        if candidate_id is None:
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

        self.context.candidates.delete(candidate_id)
        remaining_records = self.context.candidates.list_records()
        selected_candidate_id = remaining_records[0].candidate_id if remaining_records else None
        self.reload(select_candidate_id=selected_candidate_id, emit_selection=False)
        if self.on_candidate_selected:
            self.on_candidate_selected(selected_candidate_id)
        if self.on_data_changed:
            self.on_data_changed()

    def _open_workspace(self) -> None:
        candidate_id = self.selected_candidate_id()
        if candidate_id is None:
            QMessageBox.information(
                self,
                _t(self.ui_language, "进入工作台", "Open Workspace"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return
        self._update_action_state()
        if self.on_candidate_selected:
            self.on_candidate_selected(candidate_id)
        if self.on_open_workspace:
            self.on_open_workspace(candidate_id)

    def _on_language_changed(self, _index: int) -> None:
        selected_language = str(self.language_combo.currentData() or "zh")
        normalized = "en" if selected_language == "en" else "zh"
        if normalized == self.ui_language:
            return
        self.context.settings.save_ui_language(normalized)
        if self.on_ui_language_changed:
            self.on_ui_language_changed(normalized)

    def _update_action_state(self) -> None:
        has_selection = self.selected_candidate_id() is not None
        self.open_workspace_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

