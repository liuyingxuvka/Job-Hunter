from __future__ import annotations

import time
import unittest

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from jobflow_desktop_app.app.widgets.async_tasks import run_busy_task

try:
    from ._helpers import get_qapp, process_events, suppress_message_boxes
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import get_qapp, process_events, suppress_message_boxes  # type: ignore


class AsyncTasksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def test_run_busy_task_executes_with_dialog_and_timeout_timer(self) -> None:
        owner = QWidget()
        owner.ui_language = "zh"
        observed: dict[str, object] = {"result": None, "finalized": False}

        def on_success(result: object) -> None:
            observed["result"] = result

        def on_finally() -> None:
            observed["finalized"] = True

        try:
            with suppress_message_boxes():
                started = run_busy_task(
                    owner,
                    title="Busy Task",
                    message="Running background work",
                    task=lambda: 42,
                    on_success=on_success,
                    on_finally=on_finally,
                    timeout_ms=1000,
                    show_dialog=True,
                )
                self.assertTrue(started)

                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    process_events()
                    if getattr(owner, "_busy_task_thread", None) is None:
                        break
                    QTest.qWait(20)
                process_events()

            self.assertEqual(observed["result"], 42)
            self.assertTrue(observed["finalized"])
            self.assertIsNone(getattr(owner, "_busy_task_thread", None))
            self.assertIsNone(getattr(owner, "_busy_task_dialog", None))
        finally:
            owner.close()
            owner.deleteLater()
            process_events()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
