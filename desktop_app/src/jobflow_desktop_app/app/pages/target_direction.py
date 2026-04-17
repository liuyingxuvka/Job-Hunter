from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QVBoxLayout,
    QWidget,
)

from ...db.repositories.candidates import CandidateRecord
from ...db.repositories.profiles import SearchProfileRecord
from ...db.repositories.settings import OpenAISettings
from ..context import AppContext
from ...ai.role_recommendations import (
    OpenAIRoleRecommendationService,
    decode_bilingual_role_name,
    decode_bilingual_description,
    is_generic_role_name,
    select_bilingual_role_name,
)
from ..dialogs.manual_role_input import ManualRoleInputDialog
from ..widgets.common import _t, make_card, make_page_title, styled_button
from ..widgets.async_tasks import run_busy_task
from . import target_direction_bilingual
from . import target_direction_profile_completion
from . import target_direction_profile_preview
from . import target_direction_profile_records
from . import target_direction_profile_ui
from . import target_direction_profile_sync
from . import target_direction_manual_add_flow
from . import target_direction_role_suggestion_flow
from . import target_direction_workspace_state

class TargetDirectionStep(QWidget):
    AI_READY_LEVEL = "ready"

    def __init__(
        self,
        context: AppContext,
        ui_language: str = "zh",
        on_data_changed: Callable[[], None] | None = None,
        on_busy_state_changed: Callable[[bool, str], None] | None = None,
    ) -> None:
        super().__init__()
        self.context = context
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.on_data_changed = on_data_changed
        self.on_busy_state_changed = on_busy_state_changed
        self.role_recommender = OpenAIRoleRecommendationService()
        self._current_candidate: CandidateRecord | None = None
        self.current_profile_id: int | None = None
        self.profile_records: list[SearchProfileRecord] = []
        self._auto_translated_profile_ids: set[int] = set()
        self._ai_busy = False
        self._ai_busy_message = ""
        self._ai_busy_candidate_id: int | None = None
        self._ai_validation_level = "idle"
        self._ai_validation_message = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(
            make_page_title(
                _t(self.ui_language, "第二步：目标岗位设立", "Step 2: Target Roles"),
                _t(
                    self.ui_language,
                    "这里先确定要往哪几个目标岗位投递。后面系统会根据这些岗位去搜索公司和公开岗位。",
                    "Define target roles first. Later search and matching will run based on these roles.",
                ),
            )
        )

        summary_card = make_card()
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(18, 16, 18, 16)
        summary_layout.setSpacing(12)
        self.summary_label = QLabel(
            _t(
                self.ui_language,
                "这里会列出几个目标岗位。每次点一次 AI 推荐，后面只会新增少量岗位，保留下来的岗位会参与后续搜索。",
                "Target roles are listed here. Each AI recommendation adds only a few new roles, and kept roles are used in downstream search.",
            )
        )
        self.summary_label.setWordWrap(True)
        self.summary_label.setObjectName("MutedLabel")
        summary_layout.addWidget(self.summary_label)

        self.profile_meta_label = QLabel(_t(self.ui_language, "请先选择当前求职者。", "Select a candidate first."))
        self.profile_meta_label.setObjectName("MutedLabel")
        self.profile_meta_label.setWordWrap(True)
        summary_layout.addWidget(self.profile_meta_label)
        layout.addWidget(summary_card)

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(16)

        left_card = make_card()
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)
        list_hint = QLabel(
            _t(
                self.ui_language,
                "勾选表示这个岗位会参与后续搜索。",
                "Checked roles will be used in downstream search.",
            )
        )
        list_hint.setObjectName("MutedLabel")
        list_hint.setWordWrap(True)
        left_layout.addWidget(list_hint)

        self.direction_list = QListWidget()
        self.direction_list.setObjectName("TargetRoleList")
        left_layout.addWidget(self.direction_list, 1)

        self.generate_issue_label = QLabel("")
        self.generate_issue_label.setWordWrap(True)
        self.generate_issue_label.setStyleSheet("color: #b42318; font-weight: 600;")
        self.generate_issue_label.hide()
        left_layout.addWidget(self.generate_issue_label)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(10)
        self.generate_directions_button = styled_button(_t(self.ui_language, "AI 推荐岗位", "AI Recommend Roles"), "primary")
        self.add_direction_button = styled_button(_t(self.ui_language, "手动添加岗位", "Add Role Manually"), "secondary")
        self.delete_direction_button = styled_button(_t(self.ui_language, "删除岗位", "Delete Role"), "danger")
        action_row.addWidget(self.generate_directions_button)
        action_row.addWidget(self.add_direction_button)
        action_row.addWidget(self.delete_direction_button)
        left_layout.addLayout(action_row)

        right_card = make_card()
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(18, 18, 18, 18)
        right_layout.setSpacing(14)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)

        self.direction_name_input = QLineEdit()
        self.direction_reason_input = QPlainTextEdit()
        self.direction_reason_input.setPlaceholderText(
            _t(
                self.ui_language,
                "这个岗位主要做什么、为什么适合这个求职者。",
                "What this role mainly does, and why it fits this candidate.",
            )
        )
        self.direction_reason_input.setMinimumHeight(150)

        form.addRow(_t(self.ui_language, "岗位名称", "Role Name"), self.direction_name_input)
        form.addRow(_t(self.ui_language, "岗位说明", "Role Description"), self.direction_reason_input)
        right_layout.addLayout(form)
        self.save_direction_button = styled_button(_t(self.ui_language, "保存岗位信息", "Save Role Info"), "primary")
        right_layout.addWidget(self.save_direction_button, 0, Qt.AlignRight)
        right_layout.addStretch(1)

        content_row.addWidget(left_card, 1)
        content_row.addWidget(right_card, 2)
        layout.addLayout(content_row, 1)

        self.generate_directions_button.clicked.connect(self._generate_role_suggestions)
        self.save_direction_button.clicked.connect(self._save_profile)
        self.add_direction_button.clicked.connect(self._add_direction)
        self.delete_direction_button.clicked.connect(self._delete_direction)
        self.direction_list.currentItemChanged.connect(self._on_profile_selected)
        self.direction_list.itemChanged.connect(self._on_item_checked_changed)

        self._set_enabled(False)

    @property
    def current_candidate_id(self) -> int | None:
        candidate = self._current_candidate
        if candidate is None or candidate.candidate_id is None:
            return None
        return int(candidate.candidate_id)

    def is_ai_busy(self) -> bool:
        return self._ai_busy

    def ai_busy_message(self) -> str:
        return self._ai_busy_message

    def is_ai_busy_for(self, candidate_id: int | None) -> bool:
        if candidate_id is None:
            return False
        return self._ai_busy and self._ai_busy_candidate_id == candidate_id

    def ai_busy_message_for(self, candidate_id: int | None) -> str:
        if not self.is_ai_busy_for(candidate_id):
            return ""
        return self._ai_busy_message

    def _set_ai_busy_state(self, busy: bool, message: str = "", candidate_id: int | None = None) -> None:
        normalized_busy = bool(busy)
        normalized_message = str(message or "").strip() if normalized_busy else ""
        normalized_candidate_id = candidate_id if candidate_id is not None else self.current_candidate_id
        if (
            self._ai_busy == normalized_busy
            and self._ai_busy_message == normalized_message
            and self._ai_busy_candidate_id == (normalized_candidate_id if normalized_busy else None)
        ):
            return
        self._ai_busy = normalized_busy
        self._ai_busy_message = normalized_message
        self._ai_busy_candidate_id = normalized_candidate_id if normalized_busy else None
        self._refresh_ai_action_state()
        if self.on_busy_state_changed:
            self.on_busy_state_changed(normalized_busy, normalized_message)

    def set_candidate(self, candidate: CandidateRecord | int | None, preserve_profile_id: int | None = None) -> None:
        target_direction_workspace_state.set_candidate(
            self,
            candidate,
            preserve_profile_id=preserve_profile_id,
        )

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (
            self.save_direction_button,
            self.add_direction_button,
            self.delete_direction_button,
            self.direction_list,
            self.direction_name_input,
            self.direction_reason_input,
        ):
            widget.setEnabled(enabled)
        self._refresh_ai_action_state()

    def set_ai_validation_state(self, message: str, level: str = "idle") -> None:
        self._ai_validation_level = str(level or "idle").strip().lower() or "idle"
        self._ai_validation_message = str(message or "").strip()
        self._refresh_ai_action_state()

    def _can_use_ai_actions(self) -> bool:
        return self.current_candidate_id is not None and self._ai_validation_level == self.AI_READY_LEVEL

    def _ai_validation_issue(self) -> str:
        if self._ai_validation_level == self.AI_READY_LEVEL:
            return ""
        return self._ai_validation_message or _t(
            self.ui_language,
            "当前 AI 状态尚未通过验证。请先等待检查完成，或到右上角“设置 / Settings”里修复后再使用 AI 功能。",
            "The current AI status has not passed validation yet. Wait for the check to finish, or fix it in the top-right Settings / 设置 before using AI features.",
        )

    def _candidate_issue(self) -> str:
        if self._current_candidate is None or self.current_candidate_id is None:
            return _t(
                self.ui_language,
                "当前还没有选中求职者，所以“AI 推荐岗位”暂时不可用。请先返回上一步选择求职者。",
                "No candidate is selected yet, so AI role recommendation is unavailable. Go back and select a candidate first.",
            )
        return ""

    def _generate_action_issue(self) -> str:
        candidate_issue = self._candidate_issue()
        if candidate_issue:
            return candidate_issue
        if self.is_ai_busy_for(self.current_candidate_id):
            return self._ai_busy_message or _t(
                self.ui_language,
                "第二步 AI 仍在生成岗位方向。请等待当前推荐完成后再继续。",
                "Step 2 AI is still generating target roles. Wait for the current recommendation to finish first.",
            )
        return self._ai_validation_issue()

    def _set_generate_action_issue(self, message: str) -> None:
        detail = str(message or "").strip()
        if self.generate_issue_label.text() != detail:
            self.generate_issue_label.setText(detail)
        self.generate_issue_label.setHidden(not bool(detail))

    def _refresh_ai_action_state(self) -> None:
        issue = self._generate_action_issue()
        self.generate_directions_button.setEnabled(not bool(issue))
        self.generate_directions_button.setToolTip(issue)
        self._set_generate_action_issue(issue)

    def _reload_profiles(self, preserve_profile_id: int | None = None) -> None:
        target_direction_workspace_state.reload_profiles(
            self,
            preserve_profile_id=preserve_profile_id,
        )

    def _find_profile(self, profile_id: int | None) -> SearchProfileRecord | None:
        return target_direction_profile_ui.find_profile(self.profile_records, profile_id)

    def _display_role_name(self, profile: SearchProfileRecord) -> str:
        return target_direction_bilingual.display_role_name(
            profile,
            self.ui_language,
            select_bilingual_role_name,
        )

    @staticmethod
    def _canonical_role_name(role_name_i18n: str, fallback_name: str = "") -> str:
        return target_direction_bilingual.canonical_role_name(role_name_i18n, fallback_name=fallback_name)

    def _complete_role_name_pair(
        self,
        name_zh: str,
        name_en: str,
        settings: OpenAISettings,
        api_base_url: str,
        use_ai: bool,
    ) -> tuple[str, str]:
        return target_direction_bilingual.complete_role_name_pair(
            self.role_recommender,
            name_zh=name_zh,
            name_en=name_en,
            settings=settings,
            api_base_url=api_base_url,
            use_ai=use_ai,
        )

    def _complete_description_pair(
        self,
        role_name: str,
        description_zh: str,
        description_en: str,
        settings: OpenAISettings,
        api_base_url: str,
        use_ai: bool,
    ) -> tuple[str, str]:
        return target_direction_bilingual.complete_description_pair(
            self.role_recommender,
            role_name=role_name,
            description_zh=description_zh,
            description_en=description_en,
            settings=settings,
            api_base_url=api_base_url,
            use_ai=use_ai,
        )

    def _load_profile(self, profile: SearchProfileRecord) -> None:
        target_direction_workspace_state.load_profile(self, profile)

    def _ensure_profile_bilingual_for_ui(self, profile: SearchProfileRecord) -> SearchProfileRecord:
        current_name = str(profile.name or "").strip()
        settings = self.context.settings.get_effective_openai_settings()
        api_base_url = self.context.settings.get_openai_base_url()
        return target_direction_profile_preview.ensure_profile_bilingual_for_ui(
            profile,
            current_name=current_name,
            settings=settings,
            api_base_url=api_base_url,
            untitled_label=_t(self.ui_language, "未命名岗位", "Untitled Role"),
            canonical_role_name=lambda role_name_i18n, fallback_name: self._canonical_role_name(
                role_name_i18n,
                fallback_name=fallback_name,
            ),
            complete_role_name_pair=lambda name_zh, name_en, settings, api_base_url, use_ai: self._complete_role_name_pair(
                name_zh=name_zh,
                name_en=name_en,
                settings=settings,
                api_base_url=api_base_url,
                use_ai=use_ai,
            ),
            complete_description_pair=lambda role_name, description_zh, description_en, settings, api_base_url, use_ai: self._complete_description_pair(
                role_name=role_name,
                description_zh=description_zh,
                description_en=description_en,
                settings=settings,
                api_base_url=api_base_url,
                use_ai=use_ai,
            ),
        )

    def shutdown_background_work(self, wait_ms: int = 8000) -> None:
        dialog = getattr(self, "_busy_task_dialog", None)
        if isinstance(dialog, QProgressDialog):
            dialog.close()
            dialog.deleteLater()
        running_thread = getattr(self, "_busy_task_thread", None)
        if isinstance(running_thread, QThread) and running_thread.isRunning():
            running_thread.wait(max(0, int(wait_ms)))
        setattr(self, "_busy_task_thread", None)
        setattr(self, "_busy_task_worker", None)
        setattr(self, "_busy_task_dialog", None)
        setattr(self, "_busy_task_relay", None)
        self._set_ai_busy_state(False)

    def _clear_profile_form(self) -> None:
        target_direction_workspace_state.clear_profile_form(self)

    def _on_profile_selected(self, current: QListWidgetItem | None, _: QListWidgetItem | None) -> None:
        target_direction_workspace_state.on_profile_selected(self, current, _)

    def _on_item_checked_changed(self, item: QListWidgetItem) -> None:
        target_direction_workspace_state.on_item_checked_changed(self, item)

    def _generate_role_suggestions(self) -> None:
        candidate_id = self.current_candidate_id
        target_direction_role_suggestion_flow.start_role_suggestion_flow(
            owner=self,
            context=self.context,
            ui_language=self.ui_language,
            current_candidate=self._current_candidate,
            role_recommender=self.role_recommender,
            generate_button=self.generate_directions_button,
            reload_profiles=self._reload_profiles,
            on_data_changed=self.on_data_changed,
            set_ai_busy_state=self._set_ai_busy_state,
            canonical_role_name=lambda role_name_i18n, fallback_name: self._canonical_role_name(
                role_name_i18n,
                fallback_name=fallback_name,
            ),
            ai_validation_issue=self._ai_validation_issue,
            candidate_still_current=lambda: self.current_candidate_id == candidate_id,
            run_busy_task_fn=run_busy_task,
        )

    def _add_direction(self) -> None:
        candidate_id = self.current_candidate_id
        target_direction_manual_add_flow.start_manual_add_flow(
            owner=self,
            context=self.context,
            ui_language=self.ui_language,
            current_candidate=self._current_candidate,
            role_recommender=self.role_recommender,
            add_button=self.add_direction_button,
            on_data_changed=self.on_data_changed,
            reload_profiles=self._reload_profiles,
            set_ai_busy_state=self._set_ai_busy_state,
            complete_role_name_pair=lambda name_zh, name_en, settings, api_base_url, use_ai: self._complete_role_name_pair(
                name_zh=name_zh,
                name_en=name_en,
                settings=settings,
                api_base_url=api_base_url,
                use_ai=use_ai,
            ),
            complete_description_pair=lambda role_name, description_zh, description_en, settings, api_base_url, use_ai: self._complete_description_pair(
                role_name=role_name,
                description_zh=description_zh,
                description_en=description_en,
                settings=settings,
                api_base_url=api_base_url,
                use_ai=use_ai,
            ),
            canonical_role_name=lambda role_name_i18n, fallback_name: self._canonical_role_name(
                role_name_i18n,
                fallback_name=fallback_name,
            ),
            is_generic_role_name=is_generic_role_name,
            dialog_factory=ManualRoleInputDialog,
            candidate_still_current=lambda: self.current_candidate_id == candidate_id,
            allow_ai_actions=lambda: not self._ai_validation_issue(),
            run_busy_task_fn=run_busy_task,
        )

    def _save_profile(self) -> None:
        if self.current_candidate_id is None:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "保存失败", "Save Failed"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return
        if self.current_profile_id is None:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "保存失败", "Save Failed"),
                _t(self.ui_language, "请先选择或创建一个岗位。", "Please select or create a role first."),
            )
            return
        current_item = self.direction_list.currentItem()
        is_active = current_item.checkState() == Qt.Checked if current_item is not None else True
        candidate = self._current_candidate
        if candidate is None or candidate.candidate_id is None:
            QMessageBox.warning(
                self,
                _t(self.ui_language, "保存失败", "Save Failed"),
                _t(
                    self.ui_language,
                    "当前求职者不存在或已失效。请重新选择后再保存岗位。",
                    "The current candidate is missing or invalid. Please reselect the candidate and save the role again.",
                ),
            )
            return
        existing_profile = self._find_profile(self.current_profile_id)
        existing_role_name_zh, existing_role_name_en = decode_bilingual_role_name(
            existing_profile.role_name_i18n if existing_profile is not None else "",
            fallback_name=existing_profile.name if existing_profile is not None else "",
        )
        existing_zh, existing_en = decode_bilingual_description(
            existing_profile.keyword_focus if existing_profile is not None else ""
        )
        edited_name = self.direction_name_input.text().strip() or _t(self.ui_language, "未命名岗位", "Untitled Role")
        edited_description = self.direction_reason_input.toPlainText().strip()
        if self.ui_language == "en":
            role_name_zh = existing_role_name_zh
            role_name_en = edited_name
            description_zh = existing_zh
            description_en = edited_description
        else:
            role_name_zh = edited_name
            role_name_en = existing_role_name_en
            description_zh = edited_description
            description_en = existing_en

        settings = self.context.settings.get_effective_openai_settings()
        api_base_url = self.context.settings.get_openai_base_url()
        use_ai = bool(settings.api_key.strip()) and not self._ai_validation_issue()
        candidate_id = int(self.current_candidate_id)
        profile_id_for_save = int(self.current_profile_id)
        dialog_title = _t(self.ui_language, "保存失败", "Save Failed")

        def _persist(
            translated_role_name_zh: str,
            translated_role_name_en: str,
            translated_description_zh: str,
            translated_description_en: str,
            preserve_profile_id: int | None = None,
        ) -> None:
            prepared = target_direction_profile_records.prepare_profile_content(
                role_name_zh=translated_role_name_zh,
                role_name_en=translated_role_name_en,
                description_zh=translated_description_zh,
                description_en=translated_description_en,
                fallback_name=edited_name,
                untitled_label=_t(self.ui_language, "未命名岗位", "Untitled Role"),
                canonical_role_name=lambda role_name_i18n, fallback_name: self._canonical_role_name(
                    role_name_i18n,
                    fallback_name=fallback_name,
                ),
                is_generic_role_name=is_generic_role_name,
            )
            if prepared.is_generic:
                QMessageBox.warning(
                    self,
                    dialog_title,
                    _t(
                        self.ui_language,
                        "岗位名称过于泛化（例如仅 Engineer/Manager）。请改成更具体的岗位方向后再保存。",
                        "Role name is too generic (for example Engineer/Manager only). "
                        "Please make it more specific before saving.",
                    ),
                )
                return
            try:
                saved_profile_id = self.context.profiles.save(
                    target_direction_profile_records.build_updated_profile_record(
                        profile_id=profile_id_for_save,
                        candidate=candidate,
                        existing_profile=existing_profile,
                        prepared=prepared,
                        is_active=is_active,
                    )
                )
            except ValueError as exc:
                QMessageBox.warning(self, dialog_title, str(exc))
                return
            preserved_selection = preserve_profile_id if preserve_profile_id is not None else saved_profile_id
            self.current_profile_id = preserved_selection
            self._reload_profiles(preserve_profile_id=preserved_selection)
            if self.on_data_changed:
                self.on_data_changed()

        if not use_ai:
            _persist(role_name_zh, role_name_en, description_zh, description_en)
            return

        self.save_direction_button.setEnabled(False)
        busy_message = _t(
            self.ui_language,
            "AI 正在补全双语岗位信息，请稍候...",
            "AI is completing bilingual role details, please wait...",
        )
        self._set_ai_busy_state(
            True,
            _t(
                self.ui_language,
                "第二步 AI 正在保存并补全岗位信息，完成前不能开始第三步搜索。",
                "Step 2 AI is saving and completing role details. Step 3 search is blocked until it finishes.",
            ),
            candidate_id=candidate_id,
        )

        def _task() -> Any:
            completed = target_direction_profile_completion.complete_profile_text(
                role_name_zh=role_name_zh,
                role_name_en=role_name_en,
                description_zh=description_zh,
                description_en=description_en,
                fallback_name=edited_name,
                settings=settings,
                api_base_url=api_base_url,
                use_ai=True,
                complete_role_name_pair=lambda name_zh, name_en, settings, api_base_url, use_ai: self._complete_role_name_pair(
                    name_zh=name_zh,
                    name_en=name_en,
                    settings=settings,
                    api_base_url=api_base_url,
                    use_ai=use_ai,
                ),
                complete_description_pair=lambda role_name, description_zh, description_en, settings, api_base_url, use_ai: self._complete_description_pair(
                    role_name=role_name,
                    description_zh=description_zh,
                    description_en=description_en,
                    settings=settings,
                    api_base_url=api_base_url,
                    use_ai=use_ai,
                ),
            )
            return {
                "role_name_zh": completed.role_name_zh,
                "role_name_en": completed.role_name_en,
                "description_zh": completed.description_zh,
                "description_en": completed.description_en,
            }

        def _on_success(result: Any) -> None:
            if self.current_candidate_id != candidate_id:
                return
            if not isinstance(result, dict):
                QMessageBox.warning(
                    self,
                    dialog_title,
                    _t(self.ui_language, "返回结果格式异常。", "Unexpected result payload."),
                )
                return
            _persist(
                str(result.get("role_name_zh") or ""),
                str(result.get("role_name_en") or ""),
                str(result.get("description_zh") or ""),
                str(result.get("description_en") or ""),
                preserve_profile_id=self.current_profile_id,
            )

        def _on_error(exc: Exception) -> None:
            QMessageBox.warning(
                self,
                dialog_title,
                _t(
                    self.ui_language,
                    f"AI 补全失败：{exc}",
                    f"AI completion failed: {exc}",
                ),
            )

        def _on_finally() -> None:
            self.save_direction_button.setEnabled(self.current_candidate_id is not None)
            self._set_ai_busy_state(False, candidate_id=candidate_id)

        started = run_busy_task(
            self,
            title=_t(self.ui_language, "保存岗位信息", "Save Role Info"),
            message=busy_message,
            task=_task,
            on_success=_on_success,
            on_error=_on_error,
            on_finally=_on_finally,
        )
        if not started:
            self.save_direction_button.setEnabled(self.current_candidate_id is not None)
            self._set_ai_busy_state(False, candidate_id=candidate_id)

    def _delete_direction(self) -> None:
        if self.current_profile_id is None:
            QMessageBox.information(
                self,
                _t(self.ui_language, "删除岗位", "Delete Role"),
                _t(self.ui_language, "请先选择一个岗位。", "Please select a role first."),
            )
            return
        answer = QMessageBox.question(
            self,
            _t(self.ui_language, "删除岗位", "Delete Role"),
            _t(self.ui_language, "确定删除当前岗位吗？", "Delete current role?"),
        )
        if answer != QMessageBox.Yes:
            return
        self.context.profiles.delete(self.current_profile_id)
        self.current_profile_id = None
        self._reload_profiles()
        if self.on_data_changed:
            self.on_data_changed()
