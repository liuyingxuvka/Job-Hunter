from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from jobflow_desktop_app.ai.model_catalog import ModelCatalogResult
from jobflow_desktop_app.app.main_window import MainWindow, _AIValidationWorker
from jobflow_desktop_app.db.repositories.settings import OpenAISettings

try:
    from ._helpers import FakeJobSearchRunner, create_candidate, get_qapp, make_temp_context, process_events, suppress_message_boxes
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import (  # type: ignore
        FakeJobSearchRunner,
        create_candidate,
        get_qapp,
        make_temp_context,
        process_events,
        suppress_message_boxes,
    )


class MainWindowLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def test_delete_unique_candidate_recreate_and_open_workspace(self) -> None:
        with make_temp_context() as context:
            original_candidate_id = create_candidate(context, name="Demo Candidate")

            fake_runner = FakeJobSearchRunner()
            with (
                patch("jobflow_desktop_app.app.main_window._AIValidationWorker.run", autospec=True, return_value=None),
                patch.object(MainWindow, "_warmup_model_catalog", autospec=True, return_value=None),
                patch.object(MainWindow, "_start_ai_health_check", autospec=True, return_value=None),
                patch("jobflow_desktop_app.app.pages.search_results.JobSearchRunner", return_value=fake_runner),
                patch("jobflow_desktop_app.app.main_window.fetch_available_models", autospec=True),
                patch("jobflow_desktop_app.app.main_window._validate_structured_model_request", autospec=True),
            ):
                window = MainWindow(context)
                self.addCleanup(window.close)

                process_events()
                self.assertEqual(window.stack.currentWidget(), window.candidate_directory_page)
                self.assertEqual(window.candidate_directory_page.current_candidate_id, original_candidate_id)
                self.assertEqual(window.current_candidate_id, original_candidate_id)

                with suppress_message_boxes(question_answer=QMessageBox.Yes):
                    QTest.mouseClick(window.candidate_directory_page.delete_button, Qt.LeftButton)
                process_events()

                self.assertEqual(window.candidate_directory_page.candidate_list.count(), 0)
                self.assertIsNone(window.candidate_directory_page.current_candidate_id)
                self.assertIsNone(window.current_candidate_id)
                self.assertEqual(window.stack.currentWidget(), window.candidate_directory_page)
                self.assertEqual(window.workspace_compact_page.body_stack.currentWidget(), window.workspace_compact_page.empty_page)

                with patch(
                "jobflow_desktop_app.app.pages.candidate_directory.QInputDialog.getText",
                    return_value=("Recreated Candidate", True),
                ):
                    QTest.mouseClick(window.candidate_directory_page.new_button, Qt.LeftButton)
                process_events()

                self.assertEqual(window.candidate_directory_page.candidate_list.count(), 1)
                recreated_candidate = context.candidates.list_records()[0]
                self.assertEqual(recreated_candidate.name, "Recreated Candidate")
                self.assertEqual(window.candidate_directory_page.current_candidate_id, recreated_candidate.candidate_id)
                self.assertEqual(window.current_candidate_id, recreated_candidate.candidate_id)
                self.assertTrue(window.candidate_directory_page.open_workspace_button.isEnabled())

                with patch.object(QMessageBox, "information", autospec=True, return_value=QMessageBox.Ok) as info_spy:
                    QTest.mouseClick(window.candidate_directory_page.open_workspace_button, Qt.LeftButton)
                    process_events()

                self.assertEqual(window.stack.currentWidget(), window.workspace_compact_page)
                self.assertEqual(window.workspace_compact_page.body_stack.currentWidget(), window.workspace_compact_page.content_page)
                self.assertEqual(window.workspace_compact_page.current_candidate_id, recreated_candidate.candidate_id)
                self.assertEqual(window.workspace_compact_page.current_candidate_id, recreated_candidate.candidate_id)
                self.assertEqual(window.workspace_compact_page.hero_title.text(), "Recreated Candidate")
                self.assertFalse(
                    any("Please select a candidate first" in str(call.args) for call in info_spy.call_args_list)
                )
                self.assertIn("Recreated Candidate", window.workspace_compact_page.hero_title.text())
                self.assertEqual(window.workspace_compact_page.results_step.current_candidate_id, recreated_candidate.candidate_id)
                self.assertEqual(window.workspace_compact_page.results_step.current_candidate_name, "Recreated Candidate")

    def test_delete_current_candidate_keeps_remaining_selection_in_sync(self) -> None:
        with make_temp_context() as context:
            create_candidate(context, name="First Candidate")
            create_candidate(context, name="Second Candidate")

            with (
                patch("jobflow_desktop_app.app.main_window._AIValidationWorker.run", autospec=True, return_value=None),
                patch.object(MainWindow, "_warmup_model_catalog", autospec=True, return_value=None),
                patch.object(MainWindow, "_start_ai_health_check", autospec=True, return_value=None),
                patch("jobflow_desktop_app.app.pages.search_results.JobSearchRunner", return_value=FakeJobSearchRunner()),
                patch("jobflow_desktop_app.app.main_window.fetch_available_models", autospec=True),
                patch("jobflow_desktop_app.app.main_window._validate_structured_model_request", autospec=True),
            ):
                window = MainWindow(context)
                self.addCleanup(window.close)

            process_events()
            self.assertIsNotNone(window.current_candidate_id)
            initial_candidate_id = window.current_candidate_id

            window.candidate_directory_page.candidate_list.setCurrentRow(1)
            process_events()
            deleted_candidate_id = window.current_candidate_id
            self.assertNotEqual(deleted_candidate_id, initial_candidate_id)

            with suppress_message_boxes(question_answer=QMessageBox.Yes):
                QTest.mouseClick(window.candidate_directory_page.delete_button, Qt.LeftButton)
            process_events()

            remaining = context.candidates.list_records()
            self.assertEqual(len(remaining), 1)
            remaining_candidate_id = remaining[0].candidate_id
            self.assertNotEqual(remaining_candidate_id, deleted_candidate_id)
            self.assertEqual(remaining_candidate_id, initial_candidate_id)
            self.assertEqual(window.candidate_directory_page.current_candidate_id, remaining_candidate_id)
            self.assertEqual(window.current_candidate_id, remaining_candidate_id)
            self.assertEqual(window.workspace_compact_page.current_candidate_id, remaining_candidate_id)
            self.assertEqual(window.workspace_compact_page.body_stack.currentWidget(), window.workspace_compact_page.content_page)

    def test_close_event_waits_for_ai_validation_shutdown_budget(self) -> None:
        with make_temp_context() as context:
            with (
                patch("jobflow_desktop_app.app.main_window._AIValidationWorker.run", autospec=True, return_value=None),
                patch.object(MainWindow, "_warmup_model_catalog", autospec=True, return_value=None),
                patch.object(MainWindow, "_start_ai_health_check", autospec=True, return_value=None),
            ):
                window = MainWindow(context)
            self.addCleanup(window.close)

            with (
                patch.object(window.workspace_compact_page, "shutdown_background_work", autospec=True) as shutdown_compact_mock,
                patch.object(window, "_shutdown_ai_validation", autospec=True) as shutdown_ai_mock,
            ):
                window.closeEvent(QCloseEvent())

            shutdown_compact_mock.assert_called_once_with(wait_ms=8000)
            shutdown_ai_mock.assert_called_once_with(wait_ms=30000)

    def test_ai_validation_worker_validates_fast_and_quality_models_without_auto_switch(self) -> None:
        with make_temp_context() as context:
            context.settings.save_openai_settings(
                OpenAISettings(
                    api_key="test-key",
                    model="gpt-5-nano",
                    quality_model="gpt-5.4",
                    api_key_source="direct",
                    api_key_env_var="",
                )
            )
            worker = _AIValidationWorker(context)
            payloads: list[dict] = []
            worker.finished.connect(lambda payload: payloads.append(payload))

            with (
                patch(
                    "jobflow_desktop_app.app.main_window.fetch_available_models",
                    autospec=True,
                    return_value=ModelCatalogResult(models=["gpt-5-mini", "gpt-5-nano", "gpt-5.4"]),
                ),
                patch(
                    "jobflow_desktop_app.app.main_window._validate_structured_model_request",
                    autospec=True,
                    side_effect=[
                        (True, ""),
                        (False, "OpenAI API request failed: HTTP 429."),
                    ],
                ) as validate_mock,
            ):
                worker.run()

            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0]["state"], "model_unverified")
            self.assertEqual(payloads[0]["fast_model"], "gpt-5-nano")
            self.assertEqual(payloads[0]["quality_model"], "gpt-5.4")
            self.assertEqual(payloads[0]["current_model"], "fast gpt-5-nano; quality gpt-5.4")
            self.assertIn("HTTP 429", payloads[0]["error"])
            self.assertEqual(validate_mock.call_count, 2)
            self.assertEqual(validate_mock.call_args_list[0].kwargs["model_id"], "gpt-5-nano")
            self.assertEqual(validate_mock.call_args_list[1].kwargs["model_id"], "gpt-5.4")
            self.assertEqual(context.settings.get_openai_settings().model, "gpt-5-nano")
            self.assertEqual(context.settings.get_openai_settings().quality_model, "gpt-5.4")

    def test_ai_validation_worker_rejects_missing_quality_model_before_request_validation(self) -> None:
        with make_temp_context() as context:
            context.settings.save_openai_settings(
                OpenAISettings(
                    api_key="test-key",
                    model="gpt-5-nano",
                    quality_model="",
                    api_key_source="direct",
                    api_key_env_var="",
                )
            )
            worker = _AIValidationWorker(context)
            payloads: list[dict] = []
            worker.finished.connect(lambda payload: payloads.append(payload))

            with (
                patch(
                    "jobflow_desktop_app.app.main_window.fetch_available_models",
                    autospec=True,
                    return_value=ModelCatalogResult(models=["gpt-5-nano", "gpt-5.4"]),
                ),
                patch(
                    "jobflow_desktop_app.app.main_window._validate_structured_model_request",
                    autospec=True,
                ) as validate_mock,
            ):
                worker.run()

            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0]["state"], "model_unverified")
            self.assertEqual(payloads[0]["fast_model"], "gpt-5-nano")
            self.assertEqual(payloads[0]["quality_model"], "")
            self.assertIn("quality model", payloads[0]["error"])
            validate_mock.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
