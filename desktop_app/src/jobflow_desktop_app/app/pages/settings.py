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

