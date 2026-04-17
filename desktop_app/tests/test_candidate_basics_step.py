from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from jobflow_desktop_app.app.pages.candidate_basics import CandidateBasicsStep

try:
    from ._helpers import create_candidate, get_qapp, make_temp_context, process_events, suppress_message_boxes
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import create_candidate, get_qapp, make_temp_context, process_events, suppress_message_boxes  # type: ignore


class CandidateBasicsStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def _select_combo_text(self, combo, text: str) -> None:
        index = combo.findText(text, Qt.MatchFixedString)
        if index < 0:
            combo.addItem(text)
            index = combo.findText(text, Qt.MatchFixedString)
        self.assertGreaterEqual(index, 0)
        combo.setCurrentIndex(index)

    def test_save_edited_candidate_data_updates_locations_resume_notes_and_callbacks(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Demo Candidate",
                base_location="Munich, Germany",
                preferred_locations="",
                notes="Original notes",
            )
            on_data_changed = Mock()
            on_candidate_saved = Mock()
            page = CandidateBasicsStep(
                context,
                ui_language="en",
                on_data_changed=on_data_changed,
                on_candidate_saved=on_candidate_saved,
            )
            self.addCleanup(page.deleteLater)

            page.set_candidate(candidate_id)
            process_events()

            self.assertEqual(page.current_candidate_id, candidate_id)
            self.assertEqual(page.form.name_input.text(), "Demo Candidate")
            self.assertEqual(page.form.email_input.text(), "demo@example.com")
            self.assertEqual(page.form.notes_input.toPlainText(), "Original notes")

            page.form.name_input.setText("Updated Candidate")
            page.form.email_input.setText("updated@example.com")
            page.form.base_location_input.clear()
            page.form.base_location_input.addItems(["Munich, Germany", "Berlin, Germany"])
            self._select_combo_text(page.form.base_location_input, "Berlin, Germany")

            page.form.preferred_location_type_combo.setCurrentIndex(
                page.form.preferred_location_type_combo.findData("city")
            )
            process_events()
            self._select_combo_text(page.form.preferred_location_input, "Berlin, Germany")
            page.form._add_preferred_location()

            page.form.preferred_location_type_combo.setCurrentIndex(
                page.form.preferred_location_type_combo.findData("remote")
            )
            process_events()
            self._select_combo_text(page.form.preferred_location_input, "Remote")
            page.form._add_preferred_location()

            resume_path = r"C:\Temp\updated-resume.pdf"
            page.form.resume_input.setText(resume_path)
            page.form.notes_input.setPlainText("Updated notes with industry focus")

            with suppress_message_boxes():
                QTest.mouseClick(page.form.save_button, Qt.LeftButton)
            process_events()

            saved = context.candidates.get(candidate_id)
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.name, "Updated Candidate")
            self.assertEqual(saved.email, "updated@example.com")
            self.assertEqual(saved.base_location, "Berlin, Germany")
            self.assertEqual(saved.active_resume_path, resume_path)
            self.assertEqual(saved.notes, "Updated notes with industry focus")
            self.assertEqual(saved.preferred_locations.splitlines(), ["Berlin, Germany", "Remote"])
            self.assertIn('"label": "Berlin, Germany"', saved.preferred_locations_struct)
            self.assertIn('"label": "Remote"', saved.preferred_locations_struct)

            self.assertEqual(page.current_candidate_id, candidate_id)
            self.assertEqual(page.form.name_input.text(), "Updated Candidate")
            self.assertEqual(page.form.email_input.text(), "updated@example.com")
            self.assertEqual(page.form.base_location_input.currentText(), "Berlin, Germany")
            self.assertEqual(page.form.resume_input.text(), resume_path)
            self.assertEqual(page.form.notes_input.toPlainText(), "Updated notes with industry focus")
            self.assertEqual(page.form.preferred_locations_list.count(), 2)
            self.assertEqual(page.form.preferred_locations_list.item(0).text(), "City · Berlin, Germany")
            self.assertEqual(page.form.preferred_locations_list.item(1).text(), "Remote · Remote")

            on_data_changed.assert_called()
            on_candidate_saved.assert_called_once_with(candidate_id)

            record = context.candidates.get(candidate_id)
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(Path(record.active_resume_path).name, "updated-resume.pdf")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
