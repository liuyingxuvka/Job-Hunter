from __future__ import annotations

import unittest
from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from jobflow_desktop_app.app.pages import target_direction_profile_sync
from jobflow_desktop_app.db.repositories.profiles import SearchProfileRecord

try:
    from ._helpers import create_candidate, create_profile, get_qapp, make_temp_context
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import create_candidate, create_profile, get_qapp, make_temp_context  # type: ignore


class TargetDirectionProfileSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def test_reload_profiles_uses_candidate_record_and_preserves_requested_selection(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Signals Candidate")
            other_candidate_id = create_candidate(context, name="Other Candidate")
            zulu_id = create_profile(context, candidate_id, name="Zulu Systems Lead", scope_profile="", is_active=True)
            alpha_id = create_profile(context, candidate_id, name="Alpha Validation Lead", scope_profile="", is_active=False)
            create_profile(context, other_candidate_id, name="Ignored Role", scope_profile="", is_active=True)

            direction_list = QListWidget()
            candidate = context.candidates.get(candidate_id)
            self.assertIsNotNone(candidate)

            result = target_direction_profile_sync.reload_profiles(
                current_candidate=candidate,
                current_profile_id=None,
                preserve_profile_id=zulu_id,
                direction_list=direction_list,
                list_for_candidate=context.profiles.list_for_candidate,
                prepare_profile=lambda profile: profile,
                display_role_name=lambda profile: profile.name,
                display_scope_label=lambda profile: "",
                untitled_label="Untitled Role",
            )

            self.assertEqual([profile.profile_id for profile in result.profile_records], [alpha_id, zulu_id])
            self.assertEqual(result.target_row, 1)
            self.assertFalse(result.should_clear_form)
            self.assertEqual(direction_list.count(), 2)
            self.assertEqual(direction_list.item(0).text(), "Alpha Validation Lead")
            self.assertEqual(direction_list.item(1).text(), "Zulu Systems Lead")
            self.assertEqual(direction_list.item(0).checkState(), Qt.Unchecked)
            self.assertEqual(direction_list.item(1).checkState(), Qt.Checked)

    def test_reload_profiles_with_missing_candidate_clears_list(self) -> None:
        direction_list = QListWidget()
        direction_list.addItem(QListWidgetItem("Existing"))

        result = target_direction_profile_sync.reload_profiles(
            current_candidate=None,
            current_profile_id=10,
            preserve_profile_id=None,
            direction_list=direction_list,
            list_for_candidate=lambda _candidate_id: [],
            prepare_profile=lambda profile: profile,
            display_role_name=lambda profile: getattr(profile, "name", ""),
            display_scope_label=lambda profile: "",
            untitled_label="Untitled Role",
        )

        self.assertEqual(result.profile_records, [])
        self.assertIsNone(result.target_row)
        self.assertTrue(result.should_clear_form)
        self.assertEqual(direction_list.count(), 0)

    def test_apply_profile_selection_loads_existing_profile_and_clears_missing_selection(self) -> None:
        load_profile = Mock()
        clear_form = Mock()
        expected_profile = SearchProfileRecord(
            profile_id=7,
            candidate_id=1,
            name="Systems Engineer",
            scope_profile="",
            target_role="Systems Engineer",
            location_preference="Munich",
            role_name_i18n="",
            keyword_focus="systems",
            is_active=True,
        )

        item = QListWidgetItem("Systems Engineer")
        item.setData(Qt.UserRole, 7)
        result = target_direction_profile_sync.apply_profile_selection(
            item,
            clear_form=clear_form,
            find_profile=lambda profile_id: expected_profile if profile_id == 7 else None,
            load_profile=load_profile,
        )
        self.assertEqual(result, 7)
        load_profile.assert_called_once_with(expected_profile)
        clear_form.assert_not_called()

        load_profile.reset_mock()
        clear_form.reset_mock()
        missing_item = QListWidgetItem("Missing")
        missing_item.setData(Qt.UserRole, 99)
        result = target_direction_profile_sync.apply_profile_selection(
            missing_item,
            clear_form=clear_form,
            find_profile=lambda _profile_id: None,
            load_profile=load_profile,
        )
        self.assertIsNone(result)
        clear_form.assert_called_once()
        load_profile.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
