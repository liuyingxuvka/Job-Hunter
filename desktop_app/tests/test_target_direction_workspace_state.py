from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit

from jobflow_desktop_app.app.pages import target_direction_workspace_state

try:
    from ._helpers import create_candidate, create_profile, get_qapp, make_temp_context
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import create_candidate, create_profile, get_qapp, make_temp_context  # type: ignore


class _FakeTargetDirectionPage:
    def __init__(self, context, ui_language: str = "zh") -> None:
        self.context = context
        self.ui_language = ui_language
        self._current_candidate = None
        self.current_profile_id = None
        self.profile_records = []
        self._auto_translated_profile_ids = set()
        self.direction_list = QListWidget()
        self.profile_meta_label = QLabel()
        self.direction_name_input = QLineEdit()
        self.direction_reason_input = QPlainTextEdit()
        self.on_data_changed = None
        self._enabled_states: list[bool] = []

    def _set_enabled(self, enabled: bool) -> None:
        self._enabled_states.append(enabled)

    def _ensure_profile_bilingual_for_ui(self, profile):
        return profile

    def _display_role_name(self, profile) -> str:
        return profile.name

    def _display_scope_label(self, profile) -> str:
        return str(getattr(profile, "scope_profile", "") or "")

    def _find_profile(self, profile_id: int | None):
        for profile in self.profile_records:
            if profile.profile_id == profile_id:
                return profile
        return None


class TargetDirectionWorkspaceStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def test_set_candidate_loads_profiles_for_current_candidate_record(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Workspace Candidate")
            other_candidate_id = create_candidate(context, name="Other Candidate")
            preserved_profile_id = create_profile(context, candidate_id, name="Zulu Systems Lead", scope_profile="", is_active=True)
            alpha_profile_id = create_profile(context, candidate_id, name="Alpha Validation Lead", scope_profile="", is_active=False)
            create_profile(context, other_candidate_id, name="Ignored Role", scope_profile="", is_active=True)

            page = _FakeTargetDirectionPage(context)
            candidate = context.candidates.get(candidate_id)
            self.assertIsNotNone(candidate)

            target_direction_workspace_state.set_candidate(
                page,
                candidate,
                preserve_profile_id=preserved_profile_id,
            )

            self.assertIsNotNone(page._current_candidate)
            self.assertEqual(page._current_candidate.candidate_id, candidate_id)
            self.assertEqual(page.current_profile_id, preserved_profile_id)
            self.assertEqual(page._enabled_states[-1], True)
            self.assertEqual(page.direction_list.count(), 2)
            self.assertEqual(page.direction_list.currentRow(), 1)
            self.assertEqual(page.direction_list.item(0).data(Qt.UserRole), alpha_profile_id)
            self.assertIn("Workspace Candidate", page.profile_meta_label.text())

    def test_set_candidate_none_clears_state_and_disables_page(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Workspace Candidate")
            page = _FakeTargetDirectionPage(context)
            page._current_candidate = context.candidates.get(candidate_id)
            page.current_profile_id = 99
            page.profile_records = [SimpleNamespace(profile_id=99, name="Old Role")]
            page.direction_list.addItem(QListWidgetItem("Old Role"))
            page.direction_name_input.setText("Old Role")
            page.direction_reason_input.setPlainText("Old description")

            target_direction_workspace_state.set_candidate(page, None)

            self.assertIsNone(page._current_candidate)
            self.assertIsNone(page.current_profile_id)
            self.assertEqual(page.profile_records, [])
            self.assertEqual(page.direction_list.count(), 0)
            self.assertEqual(page.direction_name_input.text(), "")
            self.assertEqual(page.direction_reason_input.toPlainText(), "")
            self.assertEqual(page._enabled_states[-1], False)
            self.assertIn("选择一个求职者", page.profile_meta_label.text())

    def test_on_item_checked_changed_persists_active_flag_for_current_candidate(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Workspace Candidate")
            profile_id = create_profile(
                context,
                candidate_id,
                name="Systems Engineer",
                scope_profile="",
                is_active=True,
            )
            page = _FakeTargetDirectionPage(context)
            page._current_candidate = context.candidates.get(candidate_id)
            page.profile_records = context.profiles.list_for_candidate(candidate_id)
            page.on_data_changed = Mock()
            item = QListWidgetItem("Systems Engineer")
            item.setData(Qt.UserRole, profile_id)
            item.setCheckState(Qt.Unchecked)

            target_direction_workspace_state.on_item_checked_changed(page, item)

            saved_profile = context.profiles.get(profile_id)
            self.assertIsNotNone(saved_profile)
            self.assertFalse(saved_profile.is_active)
            self.assertEqual([profile.profile_id for profile in page.profile_records], [profile_id])
            page.on_data_changed.assert_called_once()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
