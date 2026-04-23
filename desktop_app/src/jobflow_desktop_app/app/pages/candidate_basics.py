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
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
from ..widgets.dialog_presenter import QtDialogPresenter


class CandidateForm(QWidget):
    BASE_LOCATION_FIXED_TYPE = "city"
    PREFERRED_LOCATION_TYPES = ("global", "remote", "region", "country", "city")

    def __init__(
        self,
        save_button_text: str = "保存基础信息",
        ui_language: str = "zh",
        *,
        compact_layout: bool = False,
        include_email: bool = True,
    ) -> None:
        super().__init__()
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.compact_layout = bool(compact_layout)
        self.include_email = bool(include_email)
        self._target_directions_cached = ""
        self._preferred_location_items: list[dict[str, str]] = []
        self._loaded_email_value = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.name_input = QLineEdit()
        self.email_input = QLineEdit()

        self.base_location_input = QComboBox()
        self.base_location_input.setEditable(False)
        self.base_location_input.setMinimumContentsLength(24)
        self.base_location_input.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        base_location_row = QWidget()
        self._mark_transparent(base_location_row)
        base_location_row_layout = QHBoxLayout(base_location_row)
        base_location_row_layout.setContentsMargins(0, 0, 0, 0)
        base_location_row_layout.setSpacing(8)
        base_location_row_layout.addWidget(self.base_location_input, 1)
        self.base_location_warning_label = QLabel("")
        self.base_location_warning_label.setObjectName("InlineWarningLabel")
        self.base_location_warning_label.setWordWrap(True)
        self.base_location_warning_label.hide()
        base_location_wrapper = QWidget()
        self._mark_transparent(base_location_wrapper)
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
        self._mark_transparent(preferred_control_row)
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
        self.preferred_locations_warning_label.setObjectName("InlineWarningLabel")
        self.preferred_locations_warning_label.setWordWrap(True)
        if self.compact_layout:
            self.preferred_locations_warning_label.hide()
        preferred_wrapper = QWidget()
        self._mark_transparent(preferred_wrapper)
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
        self._mark_transparent(resume_row)
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

        self.actions_row = QHBoxLayout()
        self.actions_row.setContentsMargins(0, 0, 0, 0)
        self.actions_row.setSpacing(8)
        self.meta_label = QLabel(_t(self.ui_language, "请选择或创建一个求职者。", "Select or create a candidate first."))
        self.meta_label.setObjectName("MutedLabel")
        self.save_button = styled_button(save_button_text, "primary")

        if self.include_email:
            self.email_input.show()
        else:
            self.email_input.hide()

        if self.compact_layout:
            self.name_input.hide()
            self._set_compact_control_heights(
                self.base_location_input,
                self.resume_input,
                self.choose_resume_button,
                self.preferred_location_type_combo,
                self.preferred_location_input,
                self.add_preferred_location_button,
                self.remove_preferred_location_button,
            )
            self.notes_input.setMinimumHeight(308)
            self.notes_input.setMaximumHeight(308)
            self.preferred_locations_list.setObjectName("CompactLocationList")
            self.preferred_locations_list.setMinimumHeight(112)
            self.preferred_locations_list.setMaximumHeight(112)

            columns_row = QWidget()
            self._mark_transparent(columns_row)
            columns_row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            columns_layout = QHBoxLayout(columns_row)
            columns_layout.setContentsMargins(0, 0, 0, 0)
            columns_layout.setSpacing(14)
            columns_layout.setAlignment(Qt.AlignTop)

            left_column = QWidget()
            left_column.setObjectName("CompactBasicsLeftColumn")
            self._mark_transparent(left_column)
            left_column.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            left_column_layout = QVBoxLayout(left_column)
            left_column_layout.setContentsMargins(0, 0, 0, 0)
            left_column_layout.setSpacing(10)
            left_column_layout.addWidget(
                self._build_field_block(_t(self.ui_language, "当前所在地", "Current Location"), base_location_wrapper)
            )
            left_column_layout.addWidget(
                self._build_field_block(_t(self.ui_language, "希望找工作的地点", "Preferred Locations"), preferred_wrapper)
            )
            left_column_layout.addWidget(
                self._build_field_block(_t(self.ui_language, "简历路径", "Resume Path"), resume_row)
            )

            right_column = QWidget()
            right_column.setObjectName("CompactBasicsRightColumn")
            self._mark_transparent(right_column)
            right_column.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            right_column_layout = QVBoxLayout(right_column)
            right_column_layout.setContentsMargins(0, 0, 0, 0)
            right_column_layout.setSpacing(10)
            right_column_layout.addWidget(
                self._build_field_block(
                    _t(self.ui_language, "职业背景 / 专业摘要", "Professional Background / Summary"),
                    self.notes_input,
                )
            )

            columns_layout.addWidget(left_column, 1, Qt.AlignTop)
            columns_layout.addWidget(right_column, 1, Qt.AlignTop)
            layout.addWidget(columns_row)
        else:
            layout.addWidget(self.meta_label)
            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
            form.setFormAlignment(Qt.AlignTop)
            form.setHorizontalSpacing(14)
            form.setVerticalSpacing(12)

            form.addRow(_t(self.ui_language, "姓名", "Name"), self.name_input)
            form.addRow(_t(self.ui_language, "邮箱", "Email"), self.email_input)
            form.addRow(_t(self.ui_language, "当前所在地", "Current Location"), base_location_wrapper)
            form.addRow(_t(self.ui_language, "希望找工作的地点", "Preferred Locations"), preferred_wrapper)
            form.addRow(_t(self.ui_language, "简历路径", "Resume Path"), resume_row)
            form.addRow(
                _t(self.ui_language, "职业背景 / 专业摘要", "Professional Background / Summary"),
                self.notes_input,
            )
            layout.addLayout(form)
            self.actions_row.addWidget(self.save_button)
            self.actions_row.addStretch(1)
            layout.addLayout(self.actions_row)

        self.choose_resume_button.clicked.connect(self._choose_resume)
        self.preferred_location_type_combo.currentIndexChanged.connect(self._on_preferred_location_type_changed)
        self.add_preferred_location_button.clicked.connect(self._add_preferred_location)
        self.remove_preferred_location_button.clicked.connect(self._remove_selected_preferred_locations)
        self._set_location_suggestions(self.base_location_input, self.BASE_LOCATION_FIXED_TYPE)
        self._on_preferred_location_type_changed()

    def _mark_transparent(self, widget: QWidget) -> None:
        widget.setProperty("transparentBg", True)
        widget.setAttribute(Qt.WA_StyledBackground, True)

    def _set_compact_control_heights(self, *widgets: QWidget) -> None:
        for widget in widgets:
            widget.setMinimumHeight(38)

    def _build_field_block(self, label_text: str, field_widget: QWidget) -> QWidget:
        wrapper = QWidget()
        self._mark_transparent(wrapper)
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(label_text)
        label.setObjectName("FieldLabel")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(field_widget)
        return wrapper

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
            self.base_location_warning_label.show()
            return
        self.base_location_warning_label.setText("")
        self.base_location_warning_label.hide()

    def _set_preferred_location_warning(self, warning_codes: list[str]) -> None:
        messages = self._warning_messages(warning_codes)
        if messages:
            self.preferred_locations_warning_label.setText(
                _t(self.ui_language, "提示：", "Note: ") + "；".join(messages)
            )
            self.preferred_locations_warning_label.show()
            return
        if self.compact_layout:
            self.preferred_locations_warning_label.setText("")
            self.preferred_locations_warning_label.hide()
            return
        self.preferred_locations_warning_label.setText(
            _t(
                self.ui_language,
                "可添加多个地点标签：Global / Region / Country / City / Remote。",
                "You can add multiple location tags: Global / Region / Country / City / Remote.",
            )
        )
        self.preferred_locations_warning_label.show()

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
        self._loaded_email_value = record.email
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
        self._loaded_email_value = ""
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
            email=self.email_input.text() if self.include_email else self._loaded_email_value,
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

class CandidateBasicsStep(QWidget):
    def __init__(
        self,
        context: AppContext,
        ui_language: str = "zh",
        on_data_changed: Callable[[], None] | None = None,
        on_candidate_saved: Callable[[int], None] | None = None,
        *,
        compact_layout: bool = False,
        include_email: bool = True,
        dialogs: Any | None = None,
    ) -> None:
        super().__init__()
        self.context = context
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.on_data_changed = on_data_changed
        self.on_candidate_saved = on_candidate_saved
        self.dialogs = dialogs or QtDialogPresenter()
        self._current_candidate: CandidateRecord | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        if not compact_layout:
            layout.addWidget(
                make_page_title(
                    _t(self.ui_language, "基本信息", "Basics"),
                    _t(
                        self.ui_language,
                        "维护求职者的地点、简历和专业摘要。",
                        "Maintain the candidate's locations, resume, and profile summary.",
                    ),
                )
            )

        card = make_card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setSpacing(12)

        self.form = CandidateForm(
            save_button_text=_t(self.ui_language, "保存基础信息", "Save Basics"),
            ui_language=self.ui_language,
            compact_layout=compact_layout,
            include_email=include_email,
        )
        if compact_layout:
            self.form.meta_label.hide()
        card_layout.addWidget(self.form)
        if compact_layout:
            compact_footer_row = QWidget()
            compact_footer_row.setObjectName("CompactBasicsFooterRow")
            compact_footer_row.setProperty("transparentBg", True)
            compact_footer_row.setAttribute(Qt.WA_StyledBackground, True)
            compact_footer_layout = QHBoxLayout(compact_footer_row)
            compact_footer_layout.setContentsMargins(0, 4, 0, 0)
            compact_footer_layout.setSpacing(10)
            compact_footer_layout.addStretch(1)
            compact_footer_layout.addWidget(self.form.save_button, 0, Qt.AlignRight)
            card_layout.addWidget(compact_footer_row)
        layout.addWidget(card)
        layout.addStretch(1)

        self.form.save_button.clicked.connect(self._save_candidate)
        self.set_candidate(None)

    @property
    def current_candidate_id(self) -> int | None:
        candidate = self._current_candidate
        if candidate is None or candidate.candidate_id is None:
            return None
        return int(candidate.candidate_id)

    def set_candidate(self, candidate: CandidateRecord | int | None) -> None:
        missing_candidate = isinstance(candidate, int) and self.context.candidates.get(int(candidate)) is None
        resolved_candidate = (
            self.context.candidates.get(int(candidate))
            if isinstance(candidate, int)
            else candidate
        )
        self._current_candidate = resolved_candidate if isinstance(resolved_candidate, CandidateRecord) else None
        if self._current_candidate is None:
            self.form.clear(
                _t(
                    self.ui_language,
                    "当前求职者不存在，请重新选择。",
                    "Candidate not found. Please select again.",
                )
                if missing_candidate
                else _t(
                    self.ui_language,
                    "请先选择一个求职者，再进入工作台。",
                    "Select a candidate first, then open the workspace.",
                )
            )
            self.form.set_form_enabled(False)
            return

        self.form.load_record(self._current_candidate)
        self.form.set_form_enabled(True)

    def _save_candidate(self) -> None:
        if self.current_candidate_id is None:
            self.dialogs.warning(
                self,
                _t(self.ui_language, "保存失败", "Save Failed"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return
        try:
            candidate_id = self.context.candidates.save(self.form.to_record(self.current_candidate_id))
        except ValueError as exc:
            self.dialogs.warning(self, _t(self.ui_language, "保存失败", "Save Failed"), str(exc))
            return

        self.set_candidate(candidate_id)
        if self.on_data_changed:
            self.on_data_changed()
        if self.on_candidate_saved:
            self.on_candidate_saved(candidate_id)
        if self.form.compact_layout:
            self.dialogs.information(
                self,
                _t(self.ui_language, "已保存", "Saved"),
                _t(
                    self.ui_language,
                    "求职者基础信息已保存。",
                    "Candidate basics saved.",
                ),
            )
