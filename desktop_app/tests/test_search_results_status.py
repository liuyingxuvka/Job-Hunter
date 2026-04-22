from __future__ import annotations

import unittest
from types import SimpleNamespace

from jobflow_desktop_app.app.pages import search_results_status


class SearchResultsStatusTests(unittest.TestCase):
    def test_search_runtime_messages_cover_running_stop_and_queued_states(self) -> None:
        running = search_results_status.search_runtime_messages(
            "zh",
            "running",
            candidate_name="Demo Candidate",
            pending_before_run=2,
            stage_label="公司池主流程",
            elapsed_text="25 秒",
        )
        self.assertIn("当前求职者：Demo Candidate。", running[0])
        self.assertIn("补完上次未完成的 2 条岗位", running[0])
        self.assertIn("后台进度：公司池主流程", running[1])

        stop_requested = search_results_status.search_runtime_messages(
            "en",
            "stop_requested",
            stage_label="Company-first stage",
            elapsed_text="30s",
        )
        self.assertIn("Stop requested", stop_requested[0])
        self.assertIn("Finishing: Company-first stage", stop_requested[1])

        queued = search_results_status.search_runtime_messages(
            "zh",
            "queued",
            duration_label="10 分钟",
            stage_label="公司池主流程",
            elapsed_text="1 分 5 秒",
        )
        self.assertIn("下一轮搜索已排队", queued[0])
        self.assertIn("下一轮已排队：10 分钟", queued[1])

    def test_format_helpers_cover_countdown_elapsed_and_stage_fallbacks(self) -> None:
        self.assertEqual(search_results_status.format_countdown_text(3661), "01:01:01")
        self.assertEqual(search_results_status.format_elapsed_text("zh", 65), "1 分 5 秒")
        self.assertEqual(search_results_status.format_elapsed_text("en", 3661), "1h 1m 1s")
        self.assertEqual(
            search_results_status.progress_stage_label("zh", "resume_pending"),
            "补完待处理岗位",
        )
        self.assertEqual(
            search_results_status.progress_stage_label("en", "unknown-stage"),
            "Background work",
        )

    def test_search_progress_text_handles_running_stop_requested_and_idle(self) -> None:
        progress = SimpleNamespace(
            status="running",
            stage="main",
            elapsed_seconds=87,
        )

        detail_text, dialog_text = search_results_status.search_progress_text(
            "en",
            progress,
            stop_requested=False,
            queued_restart=False,
            selected_duration_label="1 hour",
        )
        self.assertIn("Background progress: Company-first stage", detail_text)
        self.assertIn("Current stage: Company-first stage", dialog_text)

        queued_detail, queued_dialog = search_results_status.search_progress_text(
            "zh",
            progress,
            stop_requested=True,
            queued_restart=True,
            queued_duration_label="10 分钟",
            selected_duration_label="1 小时",
        )
        self.assertIn("下一轮已排队：10 分钟", queued_detail)
        self.assertIn("下一轮搜索已经排队", queued_dialog)

        idle_detail, idle_dialog = search_results_status.search_progress_text(
            "zh",
            SimpleNamespace(status="idle"),
            stop_requested=False,
            queued_restart=False,
            selected_duration_label="1 小时",
        )
        self.assertEqual((idle_detail, idle_dialog), ("", ""))

    def test_search_completion_messages_cover_pending_and_no_qualified_states(self) -> None:
        pending_detail = search_results_status.search_completion_detail(
            "zh",
            discovered_job_count=8,
            scored_job_count=0,
            recommended_job_count=0,
            pending_job_count=8,
            candidate_company_pool_count=5,
        )
        self.assertIn("本轮找到 8 条", pending_detail)
        self.assertIn("当前公司池 5 家", pending_detail)
        self.assertIn("建议现在继续搜索", pending_detail)

        no_qualified_detail = search_results_status.search_completion_detail(
            "en",
            discovered_job_count=0,
            scored_job_count=0,
            recommended_job_count=0,
            pending_job_count=0,
            candidate_company_pool_count=5,
            no_qualified_company_stop=True,
        )
        self.assertIn("No new qualified companies were found", no_qualified_detail)

        popup_text = search_results_status.search_completion_popup_message(
            "zh",
            detail_text=pending_detail,
        )
        self.assertIn("本轮搜索已结束。", popup_text)
        self.assertIn("本轮找到 8 条", popup_text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
