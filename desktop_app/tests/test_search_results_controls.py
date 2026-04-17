from __future__ import annotations

import threading
import unittest
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

from PySide6.QtCore import QThread, Qt
from PySide6.QtTest import QTest

from jobflow_desktop_app.app.pages.search_results import SearchResultsStep

try:
    from ._helpers import (
        FakeJobSearchRunner,
        create_candidate,
        create_profile,
        get_qapp,
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
        make_temp_context,
        process_events,
        save_openai_settings,
    )


class _BlockingThread(QThread):
    def __init__(self) -> None:
        super().__init__()
        self._release = threading.Event()

    def run(self) -> None:  # pragma: no cover - trivial wait loop
        self._release.wait(10)

    def release(self) -> None:
        self._release.set()


class _BusyTaskController:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.threads: list[_BlockingThread] = []
        self.last_task = None
        self.last_on_success = None
        self.last_on_error = None
        self.last_on_finally = None

    def __call__(
        self,
        owner,
        *,
        title,
        message,
        task,
        on_success,
        on_error=None,
        on_finally=None,
        timeout_ms=None,
        show_dialog=True,
    ) -> bool:
        thread = _BlockingThread()
        thread.start()
        owner._busy_task_thread = thread
        owner._search_session.phase = "running"
        self.threads.append(thread)
        self.calls.append(
            {
                "owner": owner,
                "title": title,
                "message": message,
                "timeout_ms": timeout_ms,
                "show_dialog": show_dialog,
            }
        )
        self.last_task = task
        self.last_on_success = on_success
        self.last_on_error = on_error
        self.last_on_finally = on_finally
        return True

    def finish_last_success(self) -> None:
        if self.last_task is None or self.last_on_success is None or self.last_on_finally is None:
            raise AssertionError("No queued busy task to finish.")
        result = self.last_task()
        self.last_on_success(result)
        thread = self.threads[-1] if self.threads else None
        if thread is not None:
            thread.release()
            thread.wait(2000)
        self.last_on_finally()

    def release_all(self) -> None:
        for thread in self.threads:
            thread.release()
            thread.wait(2000)


class SearchResultsControlsTests(unittest.TestCase):
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

    @contextmanager
    def _patched_busy_task(self, controller: _BusyTaskController) -> Iterator[None]:
        with patch("jobflow_desktop_app.app.pages.search_results.run_busy_task", new=controller):
            yield

    def test_start_stop_and_queued_restart_control_flow(self) -> None:
        controller = _BusyTaskController()
        with make_temp_context() as context:
            save_openai_settings(context, api_key="test-key", model="gpt-5-nano")
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            fake_runner = FakeJobSearchRunner()
            fake_runner.load_live_jobs = lambda candidate_id: []  # type: ignore[method-assign]

            step = self._make_step(context, fake_runner)
            self.addCleanup(controller.release_all)
            with self._patched_busy_task(controller):
                step.set_candidate(context.candidates.get(candidate_id))
                process_events()

                self.assertEqual(step.refresh_button.text(), "开始搜索")
                self.assertTrue(step.refresh_button.isEnabled())
                self.assertTrue(step.search_duration_combo.isEnabled())
                self.assertEqual(step.search_countdown_value_label.text(), "00:00:00")

                QTest.mouseClick(step.refresh_button, Qt.LeftButton)
                process_events()

                self.assertEqual(step.refresh_button.text(), "停止搜索")
                self.assertTrue(step.refresh_button.isEnabled())
                self.assertFalse(step.search_duration_combo.isEnabled())
                self.assertIn("后台搜索岗位", step.results_meta_label.text())
                self.assertRegex(step.search_countdown_value_label.text(), r"^\d\d:\d\d:\d\d$")

                QTest.mouseClick(step.refresh_button, Qt.LeftButton)
                process_events()

                self.assertEqual(step.refresh_button.text(), "开始搜索")
                self.assertTrue(step.refresh_button.isEnabled())
                self.assertTrue(step.search_duration_combo.isEnabled())
                self.assertIn("已请求停止", step.results_meta_label.text())
                self.assertIn("后台收尾", step.results_progress_label.text())

                QTest.mouseClick(step.refresh_button, Qt.LeftButton)
                process_events()

                self.assertEqual(step.refresh_button.text(), "停止搜索")
                self.assertTrue(step.refresh_button.isEnabled())
                self.assertFalse(step.search_duration_combo.isEnabled())
                self.assertIn("下一轮搜索已排队", step.results_meta_label.text())
                self.assertIn("下一轮已排队", step.results_progress_label.text())
                self.assertGreaterEqual(len(controller.calls), 1)

                controller.finish_last_success()
                process_events()
                process_events()

                self.assertGreaterEqual(len(controller.calls), 2)
                self.assertEqual(step.refresh_button.text(), "停止搜索")
                self.assertTrue(step.refresh_button.isEnabled())
                self.assertFalse(step.search_duration_combo.isEnabled())
                self.assertIn("正在后台搜索岗位", step.results_meta_label.text())
                self.assertIn("后台进度", step.results_progress_label.text())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
