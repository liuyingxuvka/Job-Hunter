from __future__ import annotations

from types import SimpleNamespace
import unittest

from jobflow_desktop_app.app.pages import search_results_live_runtime
from jobflow_desktop_app.app.pages.search_results_runtime_state import SearchSessionState

try:
    from ._helpers import FakeJobSearchRunner, make_job, make_temp_context
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import FakeJobSearchRunner, make_job, make_temp_context  # type: ignore


class _FakePage:
    def __init__(self, runner, jobs: list[object]) -> None:
        self.ui_language = "zh"
        self.current_candidate_id = 1
        self.hidden_job_keys: set[str] = set()
        self._search_session = SearchSessionState(
            phase="running",
            owner_candidate_id=1,
        )
        self._selected_search_duration_label = lambda: "1 小时"
        self.runner = runner
        self._live_results_last_count = len(jobs)
        self._live_results_detail_text = "后台进度：当前临时结果 1 条。"
        self._live_results_progress_signature = (
            "后台进度：公司池主流程",
            "系统正在后台搜索岗位，当前阶段：公司池主流程。你可以继续操作，搜索会在后台持续运行。",
        )
        self._live_results_signature = ()
        self.stats_refreshes = 0
        self.progress_detail_calls: list[str] = []
        self.busy_messages: list[str] = []
        self.render_calls = 0
        self.rendered_visible_jobs: list[list[object]] = []

    def _refresh_results_stats_label(self) -> None:
        self.stats_refreshes += 1

    def _set_busy_task_message(self, text: str) -> None:
        self.busy_messages.append(text)

    def _set_results_progress_detail(self, text: str) -> None:
        self.progress_detail_calls.append(text)
        self._live_results_detail_text = text

    def _render_visible_jobs(self, visible_jobs: list[object]) -> int:
        self.render_calls += 1
        self.rendered_visible_jobs.append(list(visible_jobs))
        self._live_results_signature = search_results_live_runtime.jobs_signature(visible_jobs)
        return len(visible_jobs)


class SearchResultsLiveRuntimeTests(unittest.TestCase):
    def test_search_progress_text_omits_elapsed_time(self) -> None:
        runner = FakeJobSearchRunner()
        runner.set_progress(SimpleNamespace(status="running", stage="main", elapsed_seconds=87))
        page = SimpleNamespace(
            ui_language="zh",
            runner=runner,
            _search_session=SearchSessionState(),
            _selected_search_duration_label=lambda: "1 小时",
        )

        detail_text, dialog_text = search_results_live_runtime.search_progress_text(page, 1)

        self.assertIn("后台进度：公司池主流程", detail_text)
        self.assertIn("当前阶段：公司池主流程", dialog_text)
        self.assertNotIn("已运行", detail_text)
        self.assertNotIn("已运行", dialog_text)
        self.assertNotIn("87", detail_text)
        self.assertNotIn("87", dialog_text)

    def test_refresh_live_results_skips_unmodified_ui_updates(self) -> None:
        with make_temp_context() as context:
            runner = FakeJobSearchRunner()
            jobs = [
                make_job(
                    title="Newest Role",
                    company="Acme Robotics",
                    url="https://example.com/jobs/new",
                    date_found="2026-04-14T12:00:00Z",
                )
            ]
            runner.load_live_jobs = lambda _candidate_id: list(jobs)  # type: ignore[method-assign]
            runner.set_progress(SimpleNamespace(status="running", stage="main", elapsed_seconds=87))
            fake_page = _FakePage(runner, jobs)
            fake_page._search_session.worker_thread = SimpleNamespace(isRunning=lambda: True)
            fake_page._is_search_running = lambda candidate_id=None: candidate_id in (None, 1)

            search_results_live_runtime.sync_live_results_signature(fake_page)
            search_results_live_runtime.refresh_live_results(fake_page)

            self.assertEqual(fake_page.stats_refreshes, 1)
            self.assertEqual(fake_page.render_calls, 0)
            self.assertEqual(fake_page.progress_detail_calls, [])
            self.assertEqual(fake_page.busy_messages, [])
            self.assertEqual(fake_page._live_results_signature, search_results_live_runtime.jobs_signature(jobs))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
