from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QStyleOptionViewItem, QWidget

from jobflow_desktop_app.app.pages.workspace_compact import CandidateWorkspaceCompactPage

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


class CandidateWorkspaceCompactPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def _select_combo_text(self, combo, text: str) -> None:
        index = combo.findText(text, Qt.MatchFixedString)
        if index < 0:
            combo.addItem(text)
            index = combo.findText(text, Qt.MatchFixedString)
        self.assertGreaterEqual(index, 0)
        combo.setCurrentIndex(index)

    def test_candidate_binding_and_ai_status_passthrough(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Compact Candidate",
                base_location="Hamburg, Germany",
                preferred_locations="",
                notes="Compact notes",
            )
            create_profile(
                context,
                candidate_id,
                name="Localization PM",
                is_active=True,
            )

            on_data_changed = Mock()
            with patch("jobflow_desktop_app.app.pages.search_results.JobSearchRunner", HermeticJobSearchRunner):
                page = CandidateWorkspaceCompactPage(
                    context,
                    ui_language="en",
                    on_data_changed=on_data_changed,
                )
            self.addCleanup(page.deleteLater)

            page.set_candidate(candidate_id)
            process_events()

            self.assertEqual(page.current_candidate_id, candidate_id)
            self.assertEqual(page.body_stack.currentWidget(), page.content_page)
            self.assertEqual(page.hero_title.text(), "Compact Candidate")
            avatar_label = page.findChild(QLabel, "CompactWorkspaceAvatar")
            self.assertIsNotNone(avatar_label)
            self.assertFalse(avatar_label.pixmap().isNull())
            self.assertIn("Hamburg, Germany", page.hero_meta.text())
            self.assertIn("Roles 1", page.hero_meta.text())
            self.assertIsNone(page.findChild(QLabel, "WorkspaceShellTitle"))
            self.assertTrue(page.basics_step.form.email_input.isHidden())
            self.assertIsNotNone(page.basics_step.form.findChild(QWidget, "CompactBasicsLeftColumn"))
            right_column = page.basics_step.form.findChild(QWidget, "CompactBasicsRightColumn")
            footer_row = page.basics_step.findChild(QWidget, "CompactBasicsFooterRow")
            self.assertIsNotNone(right_column)
            self.assertIsNotNone(footer_row)
            self.assertTrue(footer_row.isAncestorOf(page.basics_step.form.save_button))
            self.assertIsNone(page.basics_step.findChild(QWidget, "CompactBasicsActionRow"))
            self.assertFalse(right_column.isAncestorOf(page.basics_step.form.save_button))
            self.assertTrue(page.basics_step.form.meta_label.isHidden())
            self.assertEqual(page.basics_step.current_candidate_id, candidate_id)
            self.assertEqual(page.target_direction_step.current_candidate_id, candidate_id)
            self.assertEqual(page.results_step.current_candidate_id, candidate_id)
            self.assertEqual(page.results_step.current_candidate_name, "Compact Candidate")
            compact_titles = [
                label.text()
                for label in page.findChildren(QLabel)
                if label.objectName() == "SectionTitle"
            ]
            self.assertNotIn("Basics", compact_titles)
            self.assertNotIn("Target Roles", compact_titles)
            self.assertNotIn("Job Search", compact_titles)

            page.basics_step.form.name_input.setText("Compact Candidate Updated")
            page.basics_step.form.base_location_input.clear()
            page.basics_step.form.base_location_input.addItems(["Hamburg, Germany", "Cologne, Germany"])
            self._select_combo_text(page.basics_step.form.base_location_input, "Cologne, Germany")
            page.basics_step.form.resume_input.setText(r"C:\Temp\compact-resume.pdf")

            with suppress_message_boxes(), patch(
                "jobflow_desktop_app.app.widgets.dialog_presenter.QMessageBox.information"
            ) as info_mock:
                QTest.mouseClick(page.basics_step.form.save_button, Qt.LeftButton)
            process_events()

            self.assertEqual(page.hero_title.text(), "Compact Candidate Updated")
            self.assertIn("Cologne, Germany", page.hero_meta.text())
            self.assertIn("compact-resume.pdf", page.hero_meta.text())
            self.assertEqual(page.results_step.current_candidate_name, "Compact Candidate Updated")
            self.assertEqual(context.candidates.get(candidate_id).email, "demo@example.com")
            on_data_changed.assert_called()
            info_mock.assert_called_once()

            page.set_ai_validation_status("No usable OpenAI API key", "missing")
            process_events()

            self.assertEqual(page.results_step._ai_validation_level, "missing")
            self.assertEqual(page.results_step._ai_validation_message, "No usable OpenAI API key")
            self.assertFalse(page.results_step.refresh_button.isEnabled())
            self.assertFalse(page.results_step.search_duration_combo.isEnabled())
            self.assertFalse(page.target_direction_step.generate_directions_button.isEnabled())
            self.assertFalse(page.results_step.results_progress_label.isHidden())
            self.assertEqual(page.results_step.results_progress_label.text(), "AI key not configured")
            self.assertFalse(page.target_direction_step.generate_issue_label.isHidden())

            page.set_ai_validation_status("Validation passed", "ready")
            process_events()

            self.assertEqual(page.results_step._ai_validation_level, "ready")
            self.assertEqual(page.results_step._ai_validation_message, "Validation passed")
            self.assertTrue(page.results_step.refresh_button.isEnabled())
            self.assertTrue(page.results_step.search_duration_combo.isEnabled())
            self.assertTrue(page.target_direction_step.generate_directions_button.isEnabled())
            self.assertTrue(page.results_step.results_progress_label.isHidden())
            self.assertTrue(page.target_direction_step.generate_issue_label.isHidden())

    def test_target_role_checkbox_click_toggles_inside_compact_workspace(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Compact Candidate",
                base_location="Hamburg, Germany",
                preferred_locations="",
                notes="Compact notes",
            )
            create_profile(
                context,
                candidate_id,
                name="Localization PM",
                is_active=True,
            )

            with patch("jobflow_desktop_app.app.pages.search_results.JobSearchRunner", HermeticJobSearchRunner):
                page = CandidateWorkspaceCompactPage(
                    context,
                    ui_language="en",
                )
            self.addCleanup(page.deleteLater)

            page.resize(1400, 900)
            page.set_candidate(candidate_id)
            page._set_step(1)
            page.show()
            process_events()

            role_list = page.target_direction_step.direction_list
            item = role_list.item(0)
            self.assertIsNotNone(item)
            option = QStyleOptionViewItem()
            option.rect = role_list.visualItemRect(item)
            click_point = role_list.itemDelegate().checkbox_rect(option).center()

            QTest.mouseClick(role_list.viewport(), Qt.LeftButton, Qt.NoModifier, click_point)
            process_events()

            self.assertEqual(item.checkState(), Qt.Unchecked)

    def test_support_dialog_content_uses_paypal_me_without_email_copy_flow(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Compact Candidate",
                base_location="Hamburg, Germany",
                preferred_locations="",
                notes="Compact notes",
            )

            with patch("jobflow_desktop_app.app.pages.search_results.JobSearchRunner", HermeticJobSearchRunner):
                page = CandidateWorkspaceCompactPage(
                    context,
                    ui_language="en",
                )
            self.addCleanup(page.deleteLater)

            page.set_candidate(candidate_id)
            process_events()

            self.assertEqual(page._support_dialog_title(), "Support")
            self.assertEqual(page._support_dialog_action_label(), "Buy me a coffee via PayPal")
            self.assertIn("https://paypal.me/Yingxuliu", page._support_dialog_message())
            self.assertNotIn("@", page._support_dialog_message())
            self.assertNotIn("Copy PayPal Account", page._support_dialog_message())
            self.assertIn("buy the developer a coffee", page._support_dialog_message())
            self.assertIn("voluntary support for project maintenance", page._support_dialog_message())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
