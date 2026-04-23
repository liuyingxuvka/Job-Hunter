from __future__ import annotations

import os
from html import escape
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...db.repositories.candidates import CandidateRecord
from ..theme import RUNTIME_STATUS_DOT_COLORS, UI_COLORS
from ..context import AppContext
from ..widgets.common import _t, make_card, make_scroll_area, styled_button
from ..dialogs.ai_settings import AISettingsDialog
from .candidate_basics import CandidateBasicsStep
from .search_results_compact import SearchResultsCompactStep
from .target_direction import TargetDirectionStep

SUPPORT_PAYPAL_EMAIL_ENV = "JOBFLOW_SUPPORT_PAYPAL_EMAIL"
SUPPORT_PAYPAL_EMAIL_SETTING_KEY = "support_paypal_email"
SUPPORT_PAYPAL_EMAIL_DEFAULT = "liu.yingxu.vka@gmail.com"


class CandidateWorkspaceCompactPage(QWidget):
    """Current candidate workspace UI."""

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
        self.step_buttons: list[QWidget] = []
        self.step_titles = [
            _t(self.ui_language, "基础信息", "Basics"),
            _t(self.ui_language, "目标岗位", "Target Roles"),
            _t(self.ui_language, "岗位搜索", "Job Search"),
        ]

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(16, 8, 16, 12)
        outer_layout.setSpacing(10)

        self.body_stack = QStackedWidget()
        outer_layout.addWidget(self.body_stack, 1)

        self.empty_page = self._build_empty_page()
        self.content_page = QWidget()
        self.content_page.setObjectName("CompactWorkspaceContent")
        content_layout = QVBoxLayout(self.content_page)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        self.hero_card = self._build_candidate_strip()
        content_layout.addWidget(self.hero_card)

        self.step_bar = self._build_step_bar()
        content_layout.addWidget(self.step_bar)

        self.step_stack = QStackedWidget()
        self.basics_step = CandidateBasicsStep(
            context,
            ui_language=self.ui_language,
            on_data_changed=on_data_changed,
            on_candidate_saved=self._on_candidate_saved,
            compact_layout=True,
            include_email=False,
        )
        self.target_direction_step = TargetDirectionStep(
            context,
            ui_language=self.ui_language,
            on_data_changed=on_data_changed,
            on_busy_state_changed=self._on_target_ai_busy_state_changed,
            show_page_title=False,
        )
        self.results_step = SearchResultsCompactStep(context, ui_language=self.ui_language)
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

        self.setStyleSheet(
            f"""
            QFrame#CompactWorkspaceHero {{
              background: qlineargradient(
                x1:0, y1:0, x2:1, y2:1,
                stop:0 {UI_COLORS["accent_primary"]},
                stop:1 {UI_COLORS["accent_secondary"]}
              );
              border-radius: 16px;
              border: none;
            }}
            QFrame#CompactWorkspaceHero QLabel,
            QFrame#CompactWorkspaceHero QWidget {{
              background: transparent;
            }}
            QLabel#CompactWorkspaceName {{
              color: {UI_COLORS["text_inverse"]};
              font-size: 18px;
              font-weight: 700;
            }}
            QLabel#CompactWorkspaceMeta {{
              color: {UI_COLORS["text_soft"]};
              font-size: 12px;
            }}
            QPushButton#CompactStepButton {{
              min-height: 30px;
              padding: 2px 10px;
              border-radius: 8px;
              border: 1px solid {UI_COLORS["border"]};
              background: {UI_COLORS["bg_card"]};
              color: {UI_COLORS["text_muted"]};
              font-size: 12px;
              font-weight: 700;
            }}
            QPushButton#CompactStepButton[activeStep="true"] {{
              background: {UI_COLORS["accent_primary"]};
              color: {UI_COLORS["text_inverse"]};
              border: 1px solid {UI_COLORS["accent_primary"]};
            }}
            QPushButton#CompactToolbarButton {{
              min-height: 30px;
              padding: 2px 10px;
              border-radius: 8px;
            }}
            """
        )

        self._set_step(2)
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

    def _build_empty_page(self) -> QWidget:
        page = make_card()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        title = QLabel(_t(self.ui_language, "还没有选中求职者", "No Candidate Selected"))
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            _t(
                self.ui_language,
                "请先在启动页选择求职者。",
                "Select a candidate on the landing page first.",
            )
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        self.go_candidates_button = styled_button(
            _t(self.ui_language, "返回求职者选择", "Back to Candidates"),
            "primary",
        )

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.go_candidates_button, 0, Qt.AlignLeft)
        layout.addStretch(1)
        return page

    def _build_candidate_strip(self) -> QWidget:
        hero = make_card()
        hero.setObjectName("CompactWorkspaceHero")
        layout = QHBoxLayout(hero)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(4)
        self.hero_title = QLabel(_t(self.ui_language, "未选择求职者", "No Candidate Selected"))
        self.hero_title.setObjectName("CompactWorkspaceName")
        self.hero_meta = QLabel("")
        self.hero_meta.setObjectName("CompactWorkspaceMeta")
        self.hero_meta.setWordWrap(True)
        self.ai_validation_status_label = QLabel("")
        self.ai_validation_status_label.setObjectName("CompactWorkspaceMeta")
        self.ai_validation_status_label.setWordWrap(True)
        left.addWidget(self.hero_title)
        left.addWidget(self.hero_meta)
        left.addWidget(self.ai_validation_status_label)
        layout.addLayout(left, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.workspace_settings_button = styled_button(
            _t(self.ui_language, "设置 / Settings", "Settings / 设置"),
            "hero",
        )
        self.switch_candidate_button = styled_button(
            _t(self.ui_language, "更换求职者", "Switch Candidate"),
            "hero",
        )
        self.support_button = styled_button(
            _t(self.ui_language, "☕ 支持开发", "☕ Support Dev"),
            "hero",
        )
        for button in (self.workspace_settings_button, self.switch_candidate_button, self.support_button):
            button.setObjectName("CompactToolbarButton")
            actions.addWidget(button)
        layout.addLayout(actions)
        return hero

    def _build_step_bar(self) -> QWidget:
        card = make_card()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        for index, title in enumerate(self.step_titles):
            button = styled_button(f"{index + 1}. {title}", "step")
            button.setObjectName("CompactStepButton")
            button.clicked.connect(lambda _checked=False, step_index=index: self._set_step(step_index))
            self.step_buttons.append(button)
            layout.addWidget(button, 1)
        return card

    def shutdown_background_work(self, wait_ms: int = 8000) -> None:
        if hasattr(self, "target_direction_step") and isinstance(self.target_direction_step, TargetDirectionStep):
            self.target_direction_step.shutdown_background_work(wait_ms=wait_ms)
        if hasattr(self, "results_step") and isinstance(self.results_step, SearchResultsCompactStep):
            self.results_step.shutdown_background_work(wait_ms=wait_ms)

    def set_ai_validation_status(self, message: str, level: str = "idle") -> None:
        dot_color = RUNTIME_STATUS_DOT_COLORS.get(level, RUNTIME_STATUS_DOT_COLORS["idle"])
        safe_message = escape(str(message or "").strip())
        self.ai_validation_status_label.setText(
            f'<span style="color: {dot_color}; font-size: 15px;">●</span> '
            f'<span style="color: #ffffff;">{safe_message}</span>'
        )
        self.ai_validation_status_label.setStyleSheet("color: #ffffff;")
        if hasattr(self, "results_step") and isinstance(self.results_step, SearchResultsCompactStep):
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
                f"{base_location} · {resume_name} · Roles {profile_count}"
            )
        else:
            self.hero_meta.setText(
                f"{base_location} · {resume_name} · 岗位数 {profile_count}"
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
            QApplication.style().unpolish(button)
            QApplication.style().polish(button)

    def _on_candidate_saved(self, candidate_id: int) -> None:
        self.set_candidate(candidate_id)

    def _on_target_ai_busy_state_changed(self, busy: bool, message: str) -> None:
        if hasattr(self, "results_step") and isinstance(self.results_step, SearchResultsCompactStep):
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
        info_lines = [message, ""]
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
