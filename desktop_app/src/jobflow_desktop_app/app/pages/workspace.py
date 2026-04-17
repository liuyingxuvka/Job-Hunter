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
from ..dialogs.ai_settings import AISettingsDialog
from .candidate_basics import CandidateBasicsStep
from .search_results import SearchResultsStep
from .target_direction import TargetDirectionStep

SUPPORT_PAYPAL_EMAIL_ENV = "JOBFLOW_SUPPORT_PAYPAL_EMAIL"
SUPPORT_PAYPAL_EMAIL_SETTING_KEY = "support_paypal_email"
SUPPORT_PAYPAL_EMAIL_DEFAULT = "liu.yingxu.vka@gmail.com"

class CandidateWorkspacePage(QWidget):
    def __init__(
        self,
        context: AppContext,
        ui_language: str = "zh",
        on_data_changed: Callable[[], None] | None = None,
        on_back_to_candidates: Callable[[], None] | None = None,
        on_ui_language_changed: Callable[[str], None] | None = None,
        on_ai_settings_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.context = context
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.on_data_changed = on_data_changed
        self.on_back_to_candidates = on_back_to_candidates
        self.on_ui_language_changed = on_ui_language_changed
        self.on_ai_settings_changed = on_ai_settings_changed
        self._current_candidate: CandidateRecord | None = None
        self.step_buttons: list[QPushButton] = []
        self.step_titles = [
            _t(self.ui_language, "基础信息", "Basics"),
            _t(self.ui_language, "目标岗位设立", "Target Roles"),
            _t(self.ui_language, "岗位搜索结果", "Search Results"),
        ]

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(20, 20, 20, 20)
        outer_layout.setSpacing(16)
        outer_layout.addWidget(
            make_page_title(
                _t(self.ui_language, "求职者工作台", "Candidate Workspace"),
                _t(
                    self.ui_language,
                    "进入之后就是这个人的单独工作台。先填基础信息，再设定目标岗位方向，然后直接查看岗位搜索结果。",
                    "This is the candidate-specific workspace. Fill basics, set target roles, then review search results.",
                ),
            )
        )

        self.body_stack = QStackedWidget()
        outer_layout.addWidget(self.body_stack, 1)

        self.empty_page = make_card()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(24, 24, 24, 24)
        empty_layout.setSpacing(12)
        empty_title = QLabel(_t(self.ui_language, "还没有选中求职者", "No Candidate Selected"))
        empty_title.setObjectName("PageTitle")
        empty_subtitle = QLabel(
            _t(
                self.ui_language,
                "请先在启动页选择一个求职者，然后再进入这个工作台。",
                "Select a candidate first, then enter this workspace.",
            )
        )
        empty_subtitle.setObjectName("PageSubtitle")
        empty_subtitle.setWordWrap(True)
        self.go_candidates_button = styled_button(
            _t(self.ui_language, "返回求职者选择", "Back to Candidates"),
            "primary",
        )
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_subtitle)
        empty_layout.addWidget(self.go_candidates_button, 0, Qt.AlignLeft)
        empty_layout.addStretch(1)

        self.content_page = QWidget()
        content_layout = QVBoxLayout(self.content_page)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        self.hero_card = make_card()
        self.hero_card.setObjectName("WorkspaceHero")
        hero_layout = QHBoxLayout(self.hero_card)
        hero_layout.setContentsMargins(22, 20, 22, 20)
        hero_layout.setSpacing(18)

        hero_text = QWidget()
        hero_text_layout = QVBoxLayout(hero_text)
        hero_text_layout.setContentsMargins(0, 0, 0, 0)
        hero_text_layout.setSpacing(6)
        self.hero_eyebrow = QLabel(_t(self.ui_language, "当前求职者", "Current Candidate"))
        self.hero_eyebrow.setObjectName("HeroEyebrow")
        self.hero_title = QLabel(_t(self.ui_language, "未选择求职者", "No Candidate Selected"))
        self.hero_title.setObjectName("HeroTitle")
        self.hero_meta = QLabel("")
        self.hero_meta.setObjectName("HeroMeta")
        self.hero_meta.setWordWrap(True)
        self.ai_validation_status_label = QLabel("")
        self.ai_validation_status_label.setObjectName("HeroAiStatus")
        self.ai_validation_status_label.setWordWrap(True)
        hero_text_layout.addWidget(self.hero_eyebrow)
        hero_text_layout.addWidget(self.hero_title)
        hero_text_layout.addWidget(self.hero_meta)
        hero_text_layout.addWidget(self.ai_validation_status_label)
        hero_layout.addWidget(hero_text, 1)

        self.switch_candidate_button = styled_button(
            _t(self.ui_language, "更换求职者", "Switch Candidate"),
            "hero",
        )
        self.workspace_settings_button = styled_button(
            _t(self.ui_language, "设置 / Settings", "Settings / 设置"),
            "hero",
        )
        self.support_button = styled_button(
            _t(self.ui_language, "☕ 支持开发", "☕ Support Dev"),
            "hero",
        )
        self.support_button.setToolTip(
            _t(
                self.ui_language,
                "如果这个工具帮到了你，可以给开发者买杯咖啡。",
                "If this tool helps you, you can buy the developer a coffee.",
            )
        )
        hero_actions = QWidget()
        hero_actions_layout = QVBoxLayout(hero_actions)
        hero_actions_layout.setContentsMargins(0, 0, 0, 0)
        hero_actions_layout.setSpacing(8)
        hero_actions_layout.addWidget(self.support_button)
        hero_actions_layout.addWidget(self.workspace_settings_button)
        hero_actions_layout.addWidget(self.switch_candidate_button)
        hero_actions_layout.addStretch(1)
        hero_layout.addWidget(hero_actions, 0, Qt.AlignTop)
        content_layout.addWidget(self.hero_card)

        step_card = make_card()
        step_layout = QHBoxLayout(step_card)
        step_layout.setContentsMargins(14, 14, 14, 14)
        step_layout.setSpacing(10)
        for index, title in enumerate(self.step_titles):
            button = styled_button(f"{index + 1}. {title}", "step")
            button.clicked.connect(lambda _checked=False, step_index=index: self._set_step(step_index))
            self.step_buttons.append(button)
            step_layout.addWidget(button)
        content_layout.addWidget(step_card)

        self.step_stack = QStackedWidget()
        self.basics_step = CandidateBasicsStep(
            context,
            ui_language=self.ui_language,
            on_data_changed=on_data_changed,
            on_candidate_saved=self._on_candidate_saved,
        )
        self.target_direction_step = TargetDirectionStep(
            context,
            ui_language=self.ui_language,
            on_data_changed=on_data_changed,
            on_busy_state_changed=self._on_target_ai_busy_state_changed,
        )
        self.results_step = SearchResultsStep(context, ui_language=self.ui_language)
        self.step_stack.addWidget(make_scroll_area(self.basics_step))
        self.step_stack.addWidget(make_scroll_area(self.target_direction_step))
        self.step_stack.addWidget(make_scroll_area(self.results_step))
        content_layout.addWidget(self.step_stack, 1)

        self.body_stack.addWidget(self.empty_page)
        self.body_stack.addWidget(self.content_page)

        self.go_candidates_button.clicked.connect(self._go_back_to_candidates)
        self.switch_candidate_button.clicked.connect(self._go_back_to_candidates)
        self.workspace_settings_button.clicked.connect(self._open_ai_settings)
        self.support_button.clicked.connect(self._show_support_dialog)
        self._set_step(0)
        self.set_ai_validation_status(
            _t(self.ui_language, "AI 状态：等待验证", "AI status: waiting for validation"),
            "idle",
        )
        self.set_candidate(None)

    @property
    def current_candidate_id(self) -> int | None:
        candidate = self._current_candidate
        if candidate is None or candidate.candidate_id is None:
            return None
        return int(candidate.candidate_id)

    def shutdown_background_work(self, wait_ms: int = 8000) -> None:
        if hasattr(self, "target_direction_step") and isinstance(self.target_direction_step, TargetDirectionStep):
            self.target_direction_step.shutdown_background_work(wait_ms=wait_ms)
        if hasattr(self, "results_step") and isinstance(self.results_step, SearchResultsStep):
            self.results_step.shutdown_background_work(wait_ms=wait_ms)

    def set_ai_validation_status(self, message: str, level: str = "idle") -> None:
        dot_palette = {
            "idle": "#94a3b8",
            "checking": "#2563eb",
            "ready": "#15803d",
            "switched": "#15803d",
            "missing": "#b91c1c",
            "warning": "#b91c1c",
            "invalid": "#b91c1c",
            "model_unverified": "#b91c1c",
            "success": "#15803d",
            "error": "#b91c1c",
        }
        dot_color = dot_palette.get(level, dot_palette["idle"])
        safe_message = escape(str(message or "").strip())
        self.ai_validation_status_label.setText(
            f'<span style="color: {dot_color}; font-size: 15px;">●</span> '
            f'<span style="color: #ffffff;">{safe_message}</span>'
        )
        self.ai_validation_status_label.setStyleSheet("color: #ffffff;")
        if hasattr(self, "results_step") and isinstance(self.results_step, SearchResultsStep):
            self.results_step.set_ai_validation_state(str(message or ""), level)
        if hasattr(self, "target_direction_step") and isinstance(self.target_direction_step, TargetDirectionStep):
            self.target_direction_step.set_ai_validation_state(str(message or ""), level)

    def set_candidate(self, candidate: CandidateRecord | int | None) -> None:
        previous_candidate_id = self.current_candidate_id
        resolved_candidate = (
            self.context.candidates.get(int(candidate))
            if isinstance(candidate, int)
            else candidate
        )
        self._current_candidate = resolved_candidate if isinstance(resolved_candidate, CandidateRecord) else None
        candidate_id = self.current_candidate_id
        if candidate_id is None:
            self.body_stack.setCurrentWidget(self.empty_page)
            self.basics_step.set_candidate(None)
            self.target_direction_step.set_candidate(None)
            self.results_step.set_candidate(None)
            self.results_step.set_target_ai_busy_state(
                self.target_direction_step.is_ai_busy_for(None),
                self.target_direction_step.ai_busy_message_for(None),
            )
            return

        profile_count = len(self.context.profiles.list_for_candidate(candidate_id))
        current_candidate = self._current_candidate
        assert current_candidate is not None
        resume_name = Path(current_candidate.active_resume_path).name if current_candidate.active_resume_path else _t(
            self.ui_language,
            "未设置简历",
            "No resume",
        )
        base_location = current_candidate.base_location or _t(
            self.ui_language,
            "未填写当前所在地",
            "Location not set",
        )
        self.hero_title.setText(current_candidate.name)
        if self.ui_language == "en":
            self.hero_meta.setText(
                f"Location: {base_location}    ·    Resume: {resume_name}    ·    Roles: {profile_count}"
            )
        else:
            self.hero_meta.setText(
                f"当前所在地：{base_location}    ·    简历：{resume_name}    ·    当前岗位数：{profile_count}"
            )

        self.basics_step.set_candidate(current_candidate)
        preserve_profile_id = None
        if previous_candidate_id == candidate_id:
            preserve_profile_id = self.target_direction_step.current_profile_id
        self.target_direction_step.set_candidate(current_candidate, preserve_profile_id=preserve_profile_id)
        self.results_step.set_candidate(current_candidate)
        self.results_step.set_target_ai_busy_state(
            self.target_direction_step.is_ai_busy_for(candidate_id),
            self.target_direction_step.ai_busy_message_for(candidate_id),
        )
        self.body_stack.setCurrentWidget(self.content_page)

    def _set_step(self, index: int) -> None:
        self.step_stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.step_buttons):
            active = button_index == index
            button.setProperty("activeStep", active)
            button.style().unpolish(button)
            button.style().polish(button)

    def _on_candidate_saved(self, candidate_id: int) -> None:
        self.set_candidate(candidate_id)

    def _on_target_ai_busy_state_changed(self, busy: bool, message: str) -> None:
        if hasattr(self, "results_step") and isinstance(self.results_step, SearchResultsStep):
            candidate_id = self.current_candidate_id
            self.results_step.set_target_ai_busy_state(
                self.target_direction_step.is_ai_busy_for(candidate_id),
                self.target_direction_step.ai_busy_message_for(candidate_id),
            )

    def _go_back_to_candidates(self) -> None:
        if self.on_back_to_candidates:
            self.on_back_to_candidates()

    def _support_paypal_email(self) -> str:
        env_email = os.environ.get(SUPPORT_PAYPAL_EMAIL_ENV, "").strip()
        if env_email:
            return env_email
        saved_email = self.context.settings.get_value(SUPPORT_PAYPAL_EMAIL_SETTING_KEY, "").strip()
        if saved_email:
            return saved_email
        return SUPPORT_PAYPAL_EMAIL_DEFAULT

    def _show_support_dialog(self) -> None:
        paypal_email = self._support_paypal_email()
        title = _t(self.ui_language, "支持开发", "Support Development")
        message = _t(
            self.ui_language,
            "这个工具的开发和维护用了大量 Codex 与本地调试成本。如果它真的帮到了你，欢迎给开发者买杯咖啡。",
            "Building and maintaining this tool takes substantial Codex usage and local debugging cost. If it genuinely helps you, you are welcome to buy the developer a coffee.",
        )
        info_lines = [
            message,
            "",
        ]
        if paypal_email:
            info_lines.extend(
                [
                    _t(
                        self.ui_language,
                        "可通过 PayPal 向下面这个账号转账：",
                        "You can send support via PayPal to this account:",
                    ),
                    paypal_email,
                ]
            )
        else:
            info_lines.append(
                _t(
                    self.ui_language,
                    "PayPal 账号暂未配置。你之后给我邮箱地址后，我可以再帮你直接写进去。",
                    "PayPal account is not configured yet. Once you provide the email address, I can wire it in directly.",
                )
            )

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.NoIcon)
        dialog.setWindowTitle(title)
        dialog.setText("\n".join(info_lines))
        copy_button = None
        if paypal_email:
            copy_button = dialog.addButton(
                _t(self.ui_language, "复制 PayPal 账号", "Copy PayPal Account"),
                QMessageBox.ActionRole,
            )
        dialog.addButton(_t(self.ui_language, "关闭", "Close"), QMessageBox.RejectRole)
        dialog.exec()

        if copy_button is not None and dialog.clickedButton() is copy_button:
            QApplication.clipboard().setText(paypal_email)
            copied_dialog = QMessageBox(self)
            copied_dialog.setIcon(QMessageBox.NoIcon)
            copied_dialog.setWindowTitle(title)
            copied_dialog.setText(
                _t(
                    self.ui_language,
                    f"PayPal 账号已复制到剪贴板：{paypal_email}",
                    f"PayPal account copied to clipboard: {paypal_email}",
                )
            )
            copied_dialog.addButton(_t(self.ui_language, "关闭", "Close"), QMessageBox.AcceptRole)
            copied_dialog.exec()

    def _open_ai_settings(self) -> None:
        previous_language = self.context.settings.get_ui_language()
        dialog = AISettingsDialog(self.context, ui_language=self.ui_language, parent=self)
        accepted = dialog.exec() == QDialog.Accepted
        if not accepted:
            return
        if self.on_ai_settings_changed:
            self.on_ai_settings_changed()
        latest_language = self.context.settings.get_ui_language()
        if latest_language != previous_language and self.on_ui_language_changed:
            self.on_ui_language_changed(latest_language)

