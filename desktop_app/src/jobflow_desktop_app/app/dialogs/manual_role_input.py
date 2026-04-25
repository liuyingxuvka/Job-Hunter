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
    ADJACENT_SCOPE,
    CORE_SCOPE,
    EXPLORATORY_SCOPE,
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
                "请输入岗位名称、大致说明，并先选择它属于核心、相邻还是探索方向。提交后会由 AI 按这个方向补全更详细的岗位说明。",
                "Enter role name and rough notes, and choose whether it is a core, adjacent, or exploratory role first. After submit, AI will enrich it in that direction.",
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
        self.scope_profile_combo = QComboBox()
        self.scope_profile_combo.addItem(
            _t(self.ui_language, "请选择岗位类型", "Select role type"),
            "",
        )
        self.scope_profile_combo.addItem(
            _t(self.ui_language, "核心岗位", "Core role"),
            CORE_SCOPE,
        )
        self.scope_profile_combo.addItem(
            _t(self.ui_language, "相邻岗位", "Adjacent role"),
            ADJACENT_SCOPE,
        )
        self.scope_profile_combo.addItem(
            _t(self.ui_language, "探索岗位", "Exploratory role"),
            EXPLORATORY_SCOPE,
        )
        form.addRow(_t(self.ui_language, "岗位名称", "Role Name"), self.role_name_input)
        form.addRow(_t(self.ui_language, "岗位类型", "Role Type"), self.scope_profile_combo)
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
        self.submit_button.clicked.connect(self._submit)

    def values(self) -> tuple[str, str]:
        return (
            self.role_name_input.text().strip(),
            self.rough_description_input.toPlainText().strip(),
        )

    def selected_scope_profile(self) -> str:
        return str(self.scope_profile_combo.currentData() or "").strip()

    def _submit(self) -> None:
        role_name, _rough_description = self.values()
        if not role_name:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "手动添加岗位", "Add Role Manually"),
                _t(self.ui_language, "岗位名称不能为空。", "Role name cannot be empty."),
            )
            self.role_name_input.setFocus()
            return
        if not self.selected_scope_profile():
            QMessageBox.warning(
                self,
                _t(self.ui_language, "手动添加岗位", "Add Role Manually"),
                _t(
                    self.ui_language,
                    "请先选择这个岗位属于核心、相邻还是探索方向。",
                    "Please choose whether this role is core, adjacent, or exploratory first.",
                ),
            )
            self.scope_profile_combo.setFocus()
            return
        self.accept()

