from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from jobflow_desktop_app.app.pages.candidate_directory import CandidateDirectoryPage

try:
    from ._helpers import (
        create_candidate,
        get_qapp,
        make_temp_context,
        process_events,
        suppress_message_boxes,
    )
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import (  # type: ignore
        create_candidate,
        get_qapp,
        make_temp_context,
        process_events,
        suppress_message_boxes,
    )


class CandidateDirectoryPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def test_create_delete_and_open_flow(self) -> None:
        with make_temp_context() as context:
            on_data_changed = Mock()
            on_candidate_selected = Mock()
            on_open_workspace = Mock()
            page = CandidateDirectoryPage(
                context,
                on_data_changed=on_data_changed,
                on_candidate_selected=on_candidate_selected,
                on_open_workspace=on_open_workspace,
            )
            self.addCleanup(page.deleteLater)

            process_events()
            self.assertEqual(page.candidate_list.count(), 0)

            with patch(
                "jobflow_desktop_app.app.pages.candidate_directory.QInputDialog.getText",
                return_value=("Alice Candidate", True),
            ):
                QTest.mouseClick(page.new_button, Qt.LeftButton)
            process_events()

            self.assertEqual(page.candidate_list.count(), 1)
            self.assertEqual(page.current_candidate_id, context.candidates.list_records()[0].candidate_id)
            self.assertTrue(page.open_workspace_button.isEnabled())
            self.assertTrue(page.delete_button.isEnabled())
            on_data_changed.assert_called()
            on_candidate_selected.assert_called_with(page.current_candidate_id)

            on_candidate_selected.reset_mock()
            on_open_workspace.reset_mock()

            QTest.mouseClick(page.open_workspace_button, Qt.LeftButton)
            process_events()

            on_candidate_selected.assert_called_with(page.current_candidate_id)
            on_open_workspace.assert_called_once_with(page.current_candidate_id)

            with suppress_message_boxes(question_answer=QMessageBox.Yes):
                QTest.mouseClick(page.delete_button, Qt.LeftButton)
            process_events()

            self.assertEqual(page.candidate_list.count(), 0)
            self.assertIsNone(page.current_candidate_id)
            self.assertFalse(page.open_workspace_button.isEnabled())
            self.assertFalse(page.delete_button.isEnabled())
            self.assertEqual(context.candidates.list_records(), [])
            on_candidate_selected.assert_called_with(None)

    def test_set_selected_candidate_id_only_syncs_ui_selection(self) -> None:
        with make_temp_context() as context:
            first_id = create_candidate(context, name="Alice")
            second_id = create_candidate(context, name="Bob")
            on_candidate_selected = Mock()
            page = CandidateDirectoryPage(
                context,
                on_candidate_selected=on_candidate_selected,
            )
            self.addCleanup(page.deleteLater)

            page.reload(select_candidate_id=first_id, emit_selection=False)
            process_events()
            on_candidate_selected.reset_mock()

            page.set_selected_candidate_id(second_id, emit_selection=False)
            process_events()

            self.assertEqual(page.selected_candidate_id(), second_id)
            self.assertEqual(page.current_candidate_id, second_id)
            on_candidate_selected.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
