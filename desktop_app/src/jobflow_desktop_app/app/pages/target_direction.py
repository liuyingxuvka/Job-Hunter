from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
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
from ..widgets.async_tasks import run_busy_task
from ..widgets.common import _t, make_card, make_page_title, styled_button
from ..widgets.dialog_presenter import QtDialogPresenter
from . import target_direction_bilingual
from . import target_direction_profile_preview
from . import target_direction_profile_records
from . import target_direction_profile_ui
from . import target_direction_profile_sync
from . import target_direction_manual_add_flow
from . import target_direction_role_suggestion_flow
from . import target_direction_recommendations
from .target_direction_role_list_delegate import TargetRoleListDelegate, TargetRoleListWidget
from . import target_direction_workspace_state
from .ai_status_messages import compact_ai_blocking_issue

class TargetDirectionStep(QWidget):
    AI_READY_LEVEL = "ready"

    def __init__(
        self,
        context: AppContext,
        ui_language: str = "zh",
        on_data_changed: Callable[[], None] | None = None,
        on_busy_state_changed: Callable[[bool, str], None] | None = None,
        dialogs: Any | None = None,
        show_page_title: bool = True,
    ) -> None:
        super().__init__()
        self.context = context
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.on_data_changed = on_data_changed
        self.on_busy_state_changed = on_busy_state_changed
        self.dialogs = dialogs or QtDialogPresenter()
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
        if show_page_title:
            layout.addWidget(
                make_page_title(
                    _t(self.ui_language, "目标岗位", "Target Roles"),
                    "",
                    title_object_name="SectionTitle",
                    subtitle_object_name="SectionSubtitle",
                )
            )

        self.profile_meta_label = QLabel("")
        self.profile_meta_label.hide()

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(12)

        left_card = make_card()
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)

        self.direction_list = TargetRoleListWidget()
        self.direction_list.setObjectName("TargetRoleList")
        self.direction_list.setItemDelegate(TargetRoleListDelegate(self.direction_list))
        left_layout.addWidget(self.direction_list, 1)

        self.generate_issue_label = QLabel("")
        self.generate_issue_label.setWordWrap(True)
        self.generate_issue_label.setObjectName("InlineErrorLabel")
        self.generate_issue_label.hide()
        left_layout.addWidget(self.generate_issue_label)

        self.generate_feedback_label = QLabel("")
        self.generate_feedback_label.setWordWrap(True)
        self.generate_feedback_label.setObjectName("InlineSuccessLabel")
        self.generate_feedback_label.hide()
        left_layout.addWidget(self.generate_feedback_label)

        self.generate_directions_button = styled_button(_t(self.ui_language, "AI 推荐", "AI Recommend"), "primary")
        self.add_direction_button = styled_button(_t(self.ui_language, "手动添加", "Add Manually"), "secondary")
        self.delete_direction_button = styled_button(_t(self.ui_language, "删除岗位", "Delete Role"), "danger")
        left_action_row = QWidget()
        left_action_row.setObjectName("TargetDirectionLeftActionRow")
        left_action_row.setProperty("transparentBg", True)
        left_action_layout = QHBoxLayout(left_action_row)
        left_action_layout.setContentsMargins(0, 4, 0, 0)
        left_action_layout.setSpacing(8)
        left_action_layout.addWidget(self.generate_directions_button)
        left_action_layout.addWidget(self.add_direction_button)
        left_action_layout.addWidget(self.delete_direction_button)
        left_action_layout.addStretch(1)
        left_layout.addWidget(left_action_row)

        right_card = make_card()
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(10)

        self.direction_name_input = QLineEdit()
        self.direction_name_input.setMinimumHeight(38)
        self.direction_reason_input = QPlainTextEdit()
        self.direction_reason_input.setPlaceholderText(
            _t(
                self.ui_language,
                "这个岗位主要做什么、为什么适合这个求职者。",
                "What this role mainly does, and why it fits this candidate.",
            )
        )
        self.direction_reason_input.setMinimumHeight(150)

        right_layout.addWidget(
            self._build_editor_field(
                _t(self.ui_language, "岗位名称", "Role Name"),
                self.direction_name_input,
            )
        )
        right_layout.addWidget(
            self._build_editor_field(
                _t(self.ui_language, "岗位说明", "Role Description"),
                self.direction_reason_input,
            ),
            1,
        )
        self.save_direction_button = styled_button(_t(self.ui_language, "保存岗位信息", "Save Role Info"), "primary")
        right_action_row = QWidget()
        right_action_row.setObjectName("TargetDirectionRightActionRow")
        right_action_row.setProperty("transparentBg", True)
        right_action_layout = QHBoxLayout(right_action_row)
        right_action_layout.setContentsMargins(0, 4, 0, 0)
        right_action_layout.setSpacing(8)
        right_action_layout.addStretch(1)
        right_action_layout.addWidget(self.save_direction_button, 0, Qt.AlignRight)
        right_layout.addWidget(right_action_row)

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
        self._set_generate_action_feedback("")
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

    def _build_editor_field(self, label_text: str, field_widget: QWidget) -> QWidget:
        wrapper = QWidget()
        wrapper.setProperty("transparentBg", True)
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(label_text)
        label.setObjectName("FieldLabel")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(field_widget)
        return wrapper

    def _show_information(self, title: str, message: str) -> None:
        self.dialogs.information(self, title, message)

    def _show_warning(self, title: str, message: str) -> None:
        self.dialogs.warning(self, title, message)

    def _confirm(self, title: str, message: str) -> bool:
        return bool(self.dialogs.confirm(self, title, message))

    def set_ai_validation_state(self, message: str, level: str = "idle") -> None:
        self._ai_validation_level = str(level or "idle").strip().lower() or "idle"
        self._ai_validation_message = str(message or "").strip()
        self._refresh_ai_action_state()

    def _can_use_ai_actions(self) -> bool:
        return self.current_candidate_id is not None and self._ai_validation_level == self.AI_READY_LEVEL

    def _ai_validation_issue(self) -> str:
        if self._ai_validation_level == self.AI_READY_LEVEL:
            return ""
        return compact_ai_blocking_issue(
            self.ui_language,
            self._ai_validation_level,
            self._ai_validation_message,
        )

    def _candidate_issue(self) -> str:
        if self._current_candidate is None or self.current_candidate_id is None:
            return _t(
                self.ui_language,
                "请先选择求职者",
                "Select a candidate first",
            )
        return ""

    def _generate_action_issue(self) -> str:
        candidate_issue = self._candidate_issue()
        if candidate_issue:
            return candidate_issue
        if self.is_ai_busy_for(self.current_candidate_id):
            return self._ai_busy_message or _t(
                self.ui_language,
                "AI 正在生成，请稍候",
                "AI is generating; please wait",
            )
        return self._ai_validation_issue()

    def _set_generate_action_issue(self, message: str) -> None:
        detail = str(message or "").strip()
        if self.generate_issue_label.text() != detail:
            self.generate_issue_label.setText(detail)
        self.generate_issue_label.setHidden(not bool(detail))

    def _set_generate_action_feedback(self, message: str) -> None:
        detail = str(message or "").strip()
        if self.generate_feedback_label.text() != detail:
            self.generate_feedback_label.setText(detail)
        self.generate_feedback_label.setHidden(not bool(detail))

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

    def _display_scope_label(self, profile: SearchProfileRecord) -> str:
        return target_direction_recommendations.scope_profile_label(
            profile.scope_profile,
            self.ui_language,
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
        settings = self.context.settings.get_quality_openai_settings()
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
        self._set_generate_action_feedback("")
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
            set_generation_feedback=self._set_generate_action_feedback,
            canonical_role_name=lambda role_name_i18n, fallback_name: self._canonical_role_name(
                role_name_i18n,
                fallback_name=fallback_name,
            ),
            ai_validation_issue=self._ai_validation_issue,
            candidate_still_current=lambda: self.current_candidate_id == candidate_id,
            run_busy_task_fn=run_busy_task,
            show_information=self._show_information,
            show_warning=self._show_warning,
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
            show_information=self._show_information,
            show_warning=self._show_warning,
            candidate_still_current=lambda: self.current_candidate_id == candidate_id,
            allow_ai_actions=lambda: not self._ai_validation_issue(),
            run_busy_task_fn=run_busy_task,
        )

    def _save_profile(self) -> None:
        if self.current_candidate_id is None:
            self._show_warning(
                _t(self.ui_language, "保存失败", "Save Failed"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return
        if self.current_profile_id is None:
            self._show_warning(
                _t(self.ui_language, "保存失败", "Save Failed"),
                _t(self.ui_language, "请先选择或创建一个岗位。", "Please select or create a role first."),
            )
            return
        current_item = self.direction_list.currentItem()
        is_active = current_item.checkState() == Qt.Checked if current_item is not None else True
        candidate = self._current_candidate
        if candidate is None or candidate.candidate_id is None:
            self._show_warning(
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
                self._show_warning(
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
                self._show_warning(dialog_title, str(exc))
                return
            preserved_selection = preserve_profile_id if preserve_profile_id is not None else saved_profile_id
            self.current_profile_id = preserved_selection
            self._reload_profiles(preserve_profile_id=preserved_selection)
            if self.on_data_changed:
                self.on_data_changed()
            return True

        saved = _persist(role_name_zh, role_name_en, description_zh, description_en)
        if saved:
            self._show_information(
                _t(self.ui_language, "已保存", "Saved"),
                _t(self.ui_language, "岗位信息已保存。", "Role information saved."),
            )

    def _delete_direction(self) -> None:
        if self.current_profile_id is None:
            self._show_information(
                _t(self.ui_language, "删除岗位", "Delete Role"),
                _t(self.ui_language, "请先选择一个岗位。", "Please select a role first."),
            )
            return
        answer = self._confirm(
            _t(self.ui_language, "删除岗位", "Delete Role"),
            _t(self.ui_language, "确定删除当前岗位吗？", "Delete current role?"),
        )
        if not answer:
            return
        self.context.profiles.delete(self.current_profile_id)
        self.current_profile_id = None
        self._reload_profiles()
        if self.on_data_changed:
            self.on_data_changed()
