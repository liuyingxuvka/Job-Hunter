from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from jobflow_desktop_app.app.main_window import MainWindow

try:
    from ._helpers import (
        create_candidate,
        get_qapp,
        make_temp_context,
        process_events,
    )
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import (  # type: ignore
        create_candidate,
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

                self.assertEqual(window.stack.currentWidget(), window.workspace_page)
                self.assertEqual(window.current_candidate_id, candidate_id)
                self.assertEqual(window.workspace_page.current_candidate_id, candidate_id)
                self.assertIn("Smoke Candidate", window.statusBar().currentMessage())
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

                self.assertEqual(window.stack.currentWidget(), window.workspace_page)
                self.assertEqual(window.current_candidate_id, candidate_id)
                self.assertEqual(window.workspace_page.current_candidate_id, candidate_id)
            finally:
                window.close()
                process_events()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
