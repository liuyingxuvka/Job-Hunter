from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyleOptionViewItem

from jobflow_desktop_app.app.main_window import MainWindow

try:
    from ._helpers import (
        FakeJobSearchRunner,
        create_candidate,
        create_profile,
        get_qapp,
        make_temp_context,
        process_events,
    )
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import (  # type: ignore
        FakeJobSearchRunner,
        create_candidate,
        create_profile,
        get_qapp,
        make_temp_context,
        process_events,
    )


class MainWindowSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def test_open_workspace_from_candidate_directory(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Smoke Candidate")

            with (
                patch("jobflow_desktop_app.app.main_window._AIValidationWorker.run", autospec=True, return_value=None),
                patch.object(MainWindow, "_warmup_model_catalog", autospec=True, return_value=None),
                patch.object(MainWindow, "_start_ai_health_check", autospec=True, return_value=None),
            ):
                window = MainWindow(context)

            try:
                process_events()

                self.assertEqual(window.stack.currentWidget(), window.candidate_directory_page)
                self.assertEqual(window.candidate_directory_page.current_candidate_id, candidate_id)
                self.assertEqual(window.current_candidate_id, candidate_id)

                QTest.mouseClick(
                    window.candidate_directory_page.open_workspace_button,
                    Qt.LeftButton,
                )
                process_events()

                self.assertEqual(window.stack.currentWidget(), window.workspace_compact_page)
                self.assertEqual(window.current_candidate_id, candidate_id)
                self.assertEqual(window.workspace_compact_page.current_candidate_id, candidate_id)
                self.assertEqual(window.workspace_compact_page.step_stack.currentIndex(), 0)
                self.assertIn("Smoke Candidate", window.statusBar().currentMessage())

                window.workspace_compact_page._set_step(2)
                window._show_candidates_page()
                QTest.mouseClick(
                    window.candidate_directory_page.open_workspace_button,
                    Qt.LeftButton,
                )
                process_events()

                self.assertEqual(window.stack.currentWidget(), window.workspace_compact_page)
                self.assertEqual(window.workspace_compact_page.step_stack.currentIndex(), 0)
            finally:
                window.close()
                process_events()

    def test_open_workspace_uses_explicit_candidate_selection(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Fallback Candidate")

            with (
                patch("jobflow_desktop_app.app.main_window._AIValidationWorker.run", autospec=True, return_value=None),
                patch.object(MainWindow, "_warmup_model_catalog", autospec=True, return_value=None),
                patch.object(MainWindow, "_start_ai_health_check", autospec=True, return_value=None),
            ):
                window = MainWindow(context)

            try:
                process_events()
                window.current_candidate_id = None
                window.candidate_directory_page.set_selected_candidate_id(candidate_id, emit_selection=False)

                window._open_workspace(candidate_id)
                process_events()

                self.assertEqual(window.stack.currentWidget(), window.workspace_compact_page)
                self.assertEqual(window.current_candidate_id, candidate_id)
                self.assertEqual(window.workspace_compact_page.current_candidate_id, candidate_id)
            finally:
                window.close()
                process_events()

    def _click_checkbox_in_target_role_list(self, role_list, item) -> None:
        option = QStyleOptionViewItem()
        option.rect = role_list.visualItemRect(item)
        click_point = role_list.itemDelegate().checkbox_rect(option).center()
        QTest.mouseClick(role_list.viewport(), Qt.LeftButton, Qt.NoModifier, click_point)
        process_events()

    def test_workspace_checkbox_click_checks_profile_in_main_window(self) -> None:
        class HermeticJobSearchRunner(FakeJobSearchRunner):
            def __init__(self, *_args, **_kwargs) -> None:
                super().__init__()

        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Toggle Candidate")
            profile_id = create_profile(
                context,
                candidate_id,
                name="Localization Program Manager",
                is_active=False,
            )

            with (
                patch("jobflow_desktop_app.app.pages.search_results.JobSearchRunner", HermeticJobSearchRunner),
                patch("jobflow_desktop_app.app.main_window._AIValidationWorker.run", autospec=True, return_value=None),
                patch.object(MainWindow, "_warmup_model_catalog", autospec=True, return_value=None),
                patch.object(MainWindow, "_start_ai_health_check", autospec=True, return_value=None),
            ):
                window = MainWindow(context)

            try:
                window.show()
                process_events()

                window._open_workspace(candidate_id)
                window.workspace_compact_page._set_step(1)
                process_events()

                role_list = window.workspace_compact_page.target_direction_step.direction_list
                item = role_list.item(0)
                self.assertIsNotNone(item)
                self.assertEqual(item.checkState(), Qt.Unchecked)

                self._click_checkbox_in_target_role_list(role_list, item)

                refreshed_item = role_list.item(0)
                self.assertIsNotNone(refreshed_item)
                self.assertEqual(refreshed_item.checkState(), Qt.Checked)
                self.assertTrue(context.profiles.get(profile_id).is_active)
            finally:
                window.close()
                process_events()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
