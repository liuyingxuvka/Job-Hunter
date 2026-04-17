from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from jobflow_desktop_app.app.pages.workspace import CandidateWorkspacePage

try:
    from ._helpers import (
        SearchProgress,
        SearchStats,
        create_candidate,
        create_profile,
        get_qapp,
        make_temp_context,
        process_events,
        suppress_message_boxes,
    )
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import (  # type: ignore
        SearchProgress,
        SearchStats,
        create_candidate,
        create_profile,
        get_qapp,
        make_temp_context,
        process_events,
        suppress_message_boxes,
    )


class HermeticJobSearchRunner:
    def __init__(self, *_args, **_kwargs) -> None:
        self.jobs = []
        self.stats = SearchStats()
        self.progress = SearchProgress()

    def load_recommended_jobs(self, candidate_id: int):
        return []

    def load_search_stats(self, candidate_id: int):
        return self.stats

    def load_search_progress(self, candidate_id: int):
        return self.progress

    def run_search(self, **kwargs):
        raise AssertionError("Search should not run in this test")


class CandidateWorkspacePageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def _select_combo_text(self, combo, text: str) -> None:
        index = combo.findText(text, Qt.MatchFixedString)
        if index < 0:
            combo.addItem(text)
            index = combo.findText(text, Qt.MatchFixedString)
        self.assertGreaterEqual(index, 0)
        combo.setCurrentIndex(index)

    def test_candidate_binding_hero_refresh_and_ai_status_passthrough(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Demo Candidate",
                base_location="Munich, Germany",
                preferred_locations="",
                notes="Original summary",
            )
            create_profile(
                context,
                candidate_id,
                name="Systems Engineer",
                is_active=True,
            )

            on_data_changed = Mock()
            with patch("jobflow_desktop_app.app.pages.search_results.JobSearchRunner", HermeticJobSearchRunner):
                page = CandidateWorkspacePage(
                    context,
                    ui_language="en",
                    on_data_changed=on_data_changed,
                )
            self.addCleanup(page.deleteLater)

            page.set_candidate(candidate_id)
            process_events()

            self.assertEqual(page.current_candidate_id, candidate_id)
            self.assertEqual(page.body_stack.currentWidget(), page.content_page)
            self.assertEqual(page.hero_title.text(), "Demo Candidate")
            self.assertIn("Location: Munich, Germany", page.hero_meta.text())
            self.assertIn("Resume: No resume", page.hero_meta.text())
            self.assertIn("Roles: 1", page.hero_meta.text())
            self.assertEqual(page.basics_step.current_candidate_id, candidate_id)
            self.assertEqual(page.target_direction_step.current_candidate_id, candidate_id)
            self.assertEqual(page.results_step.current_candidate_id, candidate_id)
            self.assertEqual(page.results_step.current_candidate_name, "Demo Candidate")

            page.basics_step.form.name_input.setText("Updated Candidate")
            page.basics_step.form.email_input.setText("updated@example.com")
            page.basics_step.form.base_location_input.clear()
            page.basics_step.form.base_location_input.addItems(["Munich, Germany", "Berlin, Germany"])
            self._select_combo_text(page.basics_step.form.base_location_input, "Berlin, Germany")
            page.basics_step.form.resume_input.setText(r"C:\Temp\updated-resume.pdf")
            page.basics_step.form.notes_input.setPlainText("Updated notes for the workspace hero")

            with suppress_message_boxes():
                QTest.mouseClick(page.basics_step.form.save_button, Qt.LeftButton)
            process_events()

            self.assertEqual(page.hero_title.text(), "Updated Candidate")
            self.assertIn("Location: Berlin, Germany", page.hero_meta.text())
            self.assertIn("Resume: updated-resume.pdf", page.hero_meta.text())
            self.assertIn("Roles: 1", page.hero_meta.text())
            self.assertEqual(page.results_step.current_candidate_name, "Updated Candidate")
            self.assertEqual(page.results_step.current_candidate_id, candidate_id)
            on_data_changed.assert_called()

            page.set_ai_validation_status("No usable OpenAI API key", "missing")
            process_events()

            self.assertEqual(page.ai_validation_status_label.text().count("No usable OpenAI API key"), 1)
            self.assertEqual(page.results_step._ai_validation_level, "missing")
            self.assertEqual(page.results_step._ai_validation_message, "No usable OpenAI API key")
            self.assertEqual(page.target_direction_step._ai_validation_level, "missing")
            self.assertEqual(page.target_direction_step._ai_validation_message, "No usable OpenAI API key")
            self.assertFalse(page.results_step.refresh_button.isEnabled())
            self.assertFalse(page.results_step.search_duration_combo.isEnabled())
            self.assertFalse(page.target_direction_step.generate_directions_button.isEnabled())
            self.assertFalse(page.results_step.results_progress_label.isHidden())
            self.assertIn("No usable OpenAI API key", page.results_step.results_progress_label.text())
            self.assertFalse(page.target_direction_step.generate_issue_label.isHidden())
            self.assertEqual(page.target_direction_step.generate_issue_label.text(), "No usable OpenAI API key")

            page.set_ai_validation_status("Validation passed", "ready")
            process_events()

            self.assertEqual(page.results_step._ai_validation_level, "ready")
            self.assertEqual(page.results_step._ai_validation_message, "Validation passed")
            self.assertEqual(page.target_direction_step._ai_validation_level, "ready")
            self.assertEqual(page.target_direction_step._ai_validation_message, "Validation passed")
            self.assertTrue(page.results_step.refresh_button.isEnabled())
            self.assertTrue(page.results_step.search_duration_combo.isEnabled())
            self.assertTrue(page.target_direction_step.generate_directions_button.isEnabled())
            self.assertTrue(page.results_step.results_progress_label.isHidden())
            self.assertTrue(page.target_direction_step.generate_issue_label.isHidden())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
