from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...db.repositories.candidates import CandidateRecord, CandidateSummary
from ..context import AppContext
from ..theme import UI_COLORS
from ..widgets.common import _t, make_card, styled_button
from ..widgets.dialog_presenter import QtDialogPresenter

class CandidateDirectoryPage(QWidget):
    def __init__(
        self,
        context: AppContext,
        ui_language: str = "zh",
        on_data_changed: Callable[[], None] | None = None,
        on_candidate_selected: Callable[[int | None], None] | None = None,
        on_open_workspace: Callable[[int], None] | None = None,
        on_ui_language_changed: Callable[[str], None] | None = None,
        dialogs: Any | None = None,
    ) -> None:
        super().__init__()
        self.context = context
        self.ui_language = "en" if ui_language == "en" else "zh"
        self.on_data_changed = on_data_changed
        self.on_candidate_selected = on_candidate_selected
        self.on_open_workspace = on_open_workspace
        self.on_ui_language_changed = on_ui_language_changed
        self.dialogs = dialogs or QtDialogPresenter()
        self.records: list[CandidateRecord] = []
        self.summaries_by_id: dict[int, CandidateSummary] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        left_card = make_card()
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)

        self.candidate_list = QListWidget()
        self.candidate_list.setObjectName("EntityList")
        self.candidate_list.setSpacing(4)
        self.candidate_list.setIconSize(QSize(34, 34))
        self.candidate_list.setStyleSheet(
            f"""
            QListWidget#EntityList {{
              padding: 6px;
            }}
            QListWidget#EntityList::item {{
              min-height: 58px;
              padding: 8px 12px;
              margin: 2px 0;
              border-radius: 12px;
              border: 1px solid transparent;
            }}
            QListWidget#EntityList::item:hover:!selected {{
              background: {UI_COLORS["bg_subtle"]};
              border: 1px solid {UI_COLORS["border"]};
            }}
            QListWidget#EntityList::item:selected {{
              background: qlineargradient(
                x1:0, y1:0, x2:1, y2:1,
                stop:0 {UI_COLORS["accent_primary"]},
                stop:1 {UI_COLORS["accent_secondary"]}
              );
              color: {UI_COLORS["text_inverse"]};
              border: 1px solid {UI_COLORS["accent_secondary"]};
            }}
            """
        )
        left_layout.addWidget(self.candidate_list, 1)

        right_card = make_card()
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(18, 18, 18, 18)
        right_layout.setSpacing(10)

        self.directory_title_label = QLabel(_t(self.ui_language, "选择求职者", "Select Candidate"))
        self.directory_title_label.setObjectName("SectionTitle")
        self.directory_subtitle_label = QLabel(
            _t(
                self.ui_language,
                "选择或新建求职者后进入工作台。",
                "Select or create a candidate, then open the workspace.",
            )
        )
        self.directory_subtitle_label.setObjectName("SectionSubtitle")
        self.directory_subtitle_label.setWordWrap(True)
        right_layout.addWidget(self.directory_title_label)
        right_layout.addWidget(self.directory_subtitle_label)

        language_row = QHBoxLayout()
        language_row.setContentsMargins(0, 0, 0, 0)
        language_row.setSpacing(6)
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
            _t(self.ui_language, "进入工作台", "Open Workspace"),
            "primary",
        )
        self.new_button = styled_button(_t(self.ui_language, "新建求职者", "New Candidate"), "secondary")
        self.rename_button = styled_button(_t(self.ui_language, "重命名", "Rename"), "secondary")
        self.delete_button = styled_button(_t(self.ui_language, "删除这个人", "Delete Candidate"), "danger")
        for button in (self.open_workspace_button, self.new_button, self.rename_button, self.delete_button):
            button.setMinimumHeight(36)
            button.setMaximumHeight(42)
            button.setFixedWidth(150)
        right_layout.addWidget(self.open_workspace_button, 0, Qt.AlignLeft)
        right_layout.addWidget(self.new_button, 0, Qt.AlignLeft)
        right_layout.addWidget(self.rename_button, 0, Qt.AlignLeft)
        right_layout.addWidget(self.delete_button, 0, Qt.AlignLeft)
        right_layout.addStretch(1)

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(14)
        content_row.addWidget(left_card, 3)
        content_row.addWidget(right_card, 1)
        layout.addLayout(content_row, 1)

        self.new_button.clicked.connect(self._new_candidate)
        self.rename_button.clicked.connect(self._rename_candidate)
        self.delete_button.clicked.connect(self._delete_candidate)
        self.open_workspace_button.clicked.connect(self._open_default_workspace)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        self.candidate_list.currentItemChanged.connect(self._on_candidate_selected)
        self.candidate_list.itemDoubleClicked.connect(lambda _: self._open_default_workspace())
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
            resume_text = _t(self.ui_language, f"简历：{resume_name}", f"Resume: {resume_name}")
            item = QListWidgetItem(f"{record.name}\n{resume_text}    ·    {role_text}")
            item.setIcon(self._candidate_avatar_icon())
            item.setSizeHint(QSize(0, 68))
            item.setData(Qt.UserRole, record.candidate_id)
            item.setToolTip(f"{record.name}\n{resume_text} · {role_text}")
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

    @staticmethod
    def _candidate_avatar_icon() -> QIcon:
        icon = QIcon()
        icon.addPixmap(
            CandidateDirectoryPage._draw_candidate_avatar(
                stroke=UI_COLORS["accent_secondary"],
                fill="#e8f1f7",
            ),
            QIcon.Normal,
        )
        icon.addPixmap(
            CandidateDirectoryPage._draw_candidate_avatar(
                stroke=UI_COLORS["text_inverse"],
                fill=QColor(255, 255, 255, 42),
            ),
            QIcon.Selected,
        )
        return icon

    @staticmethod
    def _draw_candidate_avatar(*, stroke: str, fill: str | QColor) -> QPixmap:
        pixmap = QPixmap(34, 34)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(stroke), 1.6))
        painter.setBrush(QColor(fill) if isinstance(fill, str) else fill)
        painter.drawEllipse(2, 2, 30, 30)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(12, 8, 10, 10)
        painter.drawArc(8, 17, 18, 12, 20 * 16, 140 * 16)
        painter.end()
        return pixmap

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
            self.dialogs.warning(
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

    def _rename_candidate(self) -> None:
        candidate_id = self.selected_candidate_id()
        if candidate_id is None:
            self.dialogs.information(
                self,
                _t(self.ui_language, "重命名求职者", "Rename Candidate"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return

        record = self.context.candidates.get(candidate_id)
        if record is None:
            self.dialogs.warning(
                self,
                _t(self.ui_language, "重命名求职者", "Rename Candidate"),
                _t(self.ui_language, "当前求职者不存在，请重新选择。", "Candidate not found. Please select again."),
            )
            self.reload(select_candidate_id=None, emit_selection=True)
            return

        name, ok = QInputDialog.getText(
            self,
            _t(self.ui_language, "重命名求职者", "Rename Candidate"),
            _t(self.ui_language, "请输入新的求职者名称：", "Enter the new candidate name:"),
            QLineEdit.Normal,
            record.name,
        )
        if not ok:
            return
        new_name = name.strip()
        if not new_name:
            self.dialogs.warning(
                self,
                _t(self.ui_language, "重命名求职者", "Rename Candidate"),
                _t(self.ui_language, "名称不能为空。", "Name cannot be empty."),
            )
            return
        if new_name == record.name:
            return

        saved_candidate_id = self.context.candidates.save(replace(record, name=new_name))
        self.reload(select_candidate_id=saved_candidate_id, emit_selection=False)
        if self.on_candidate_selected:
            self.on_candidate_selected(saved_candidate_id)
        if self.on_data_changed:
            self.on_data_changed()

    def _delete_candidate(self) -> None:
        candidate_id = self.selected_candidate_id()
        if candidate_id is None:
            self.dialogs.information(
                self,
                _t(self.ui_language, "删除求职者", "Delete Candidate"),
                _t(self.ui_language, "请先选择一个求职者。", "Please select a candidate first."),
            )
            return
        confirmed = self.dialogs.confirm(
            self,
            _t(self.ui_language, "删除求职者", "Delete Candidate"),
            _t(
                self.ui_language,
                "删除求职者会同时删除这个人的简历、目标岗位和关联状态。确定继续吗？",
                "Deleting this candidate will also remove resume, target roles, and related states. Continue?",
            ),
        )
        if not confirmed:
            return

        self.context.candidates.delete(candidate_id)
        remaining_records = self.context.candidates.list_records()
        selected_candidate_id = remaining_records[0].candidate_id if remaining_records else None
        self.reload(select_candidate_id=selected_candidate_id, emit_selection=False)
        if self.on_candidate_selected:
            self.on_candidate_selected(selected_candidate_id)
        if self.on_data_changed:
            self.on_data_changed()

    def _open_default_workspace(self) -> None:
        candidate_id = self.selected_candidate_id()
        if candidate_id is None:
            self.dialogs.information(
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
        self.rename_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

