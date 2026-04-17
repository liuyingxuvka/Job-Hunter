from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from jobflow_desktop_app.app.pages.search_results import SearchResultsStep

try:
    from ._helpers import (
        FakeJobSearchRunner,
        create_candidate,
        create_profile,
        get_qapp,
        make_job,
        make_temp_context,
        process_events,
        save_openai_settings,
    )
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import (  # type: ignore
        FakeJobSearchRunner,
        create_candidate,
        create_profile,
        get_qapp,
        make_job,
        make_temp_context,
        process_events,
        save_openai_settings,
    )


class SearchResultsStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def _make_step(self, context, fake_runner: FakeJobSearchRunner) -> SearchResultsStep:
        with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
            return_value=fake_runner,
        ):
            step = SearchResultsStep(context, ui_language="zh")
        self.addCleanup(step.deleteLater)
        return step

    def test_red_ai_state_blocks_search_button(self) -> None:
        with make_temp_context() as context:
            save_openai_settings(context, api_key="test-key", model="gpt-5-nano")
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)

            fake_runner = FakeJobSearchRunner()
            fake_runner.load_live_jobs = lambda candidate_id: []  # type: ignore[method-assign]
            fake_runner.set_jobs([make_job(title="Alpha Engineer")])

            step = self._make_step(context, fake_runner)
            step.set_candidate(context.candidates.get(candidate_id))
            process_events()
            self.assertTrue(step.refresh_button.isEnabled())

            step.set_ai_validation_state("AI validation failed", level="error")
            process_events()

            self.assertFalse(step.refresh_button.isEnabled())
            self.assertFalse(step.search_duration_combo.isEnabled())
            self.assertIn("AI validation failed", step.refresh_button.toolTip())
            self.assertIn("AI validation failed", step.results_progress_label.text())

    def test_no_candidate_shows_visible_reason_and_disables_search_controls(self) -> None:
        with make_temp_context() as context:
            fake_runner = FakeJobSearchRunner()
            step = self._make_step(context, fake_runner)

            step.set_candidate(None)
            process_events()

            self.assertFalse(step.refresh_button.isEnabled())
            self.assertFalse(step.search_duration_combo.isEnabled())
            self.assertFalse(step.results_progress_label.isHidden())
            self.assertIn("开始搜索", step.results_progress_label.text())
            self.assertIn("开始搜索", step.refresh_button.toolTip())

    def test_render_delete_and_reload_persists_hidden_rows(self) -> None:
        with make_temp_context() as context:
            save_openai_settings(context, api_key="test-key", model="gpt-5-nano")
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)

            fake_runner = FakeJobSearchRunner()
            fake_runner.load_live_jobs = lambda candidate_id: []  # type: ignore[method-assign]
            fake_runner.set_jobs(
                [
                    make_job(
                        title="Systems Engineer",
                        company="Acme Robotics",
                        url="https://example.com/jobs/new",
                        date_found="2026-04-14T12:00:00Z",
                    ),
                    make_job(
                        title="Validation Engineer",
                        company="Acme Robotics",
                        url="https://example.com/jobs/old",
                        date_found="2026-04-13T12:00:00Z",
                    ),
                ]
            )

            step = self._make_step(context, fake_runner)
            step.set_candidate(context.candidates.get(candidate_id))
            process_events()

            self.assertEqual(step.table.rowCount(), 2)
            self.assertEqual(step.table.item(0, 0).text(), "Systems Engineer")
            self.assertEqual(step.table.item(1, 0).text(), "Validation Engineer")
            self.assertTrue(step.refresh_button.isEnabled())

            step.table.selectRow(0)
            process_events()
            QTest.mouseClick(step.delete_button, Qt.LeftButton)
            process_events()

            self.assertEqual(step.table.rowCount(), 1)
            self.assertEqual(step.hidden_job_keys, {"https://example.com/jobs/new"})

            reloaded_step = self._make_step(context, fake_runner)
            reloaded_step.set_candidate(context.candidates.get(candidate_id))
            process_events()

            self.assertEqual(reloaded_step.table.rowCount(), 1)
            self.assertEqual(reloaded_step.table.item(0, 0).text(), "Validation Engineer")
            self.assertEqual(reloaded_step.hidden_job_keys, {"https://example.com/jobs/new"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
