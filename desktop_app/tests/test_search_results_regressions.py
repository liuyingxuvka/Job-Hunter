from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import QThread, Qt
from PySide6.QtTest import QTest

from jobflow_desktop_app.app.pages.search_results import SearchResultsStep
from jobflow_desktop_app.db.repositories.search_runtime import JobReviewStateRepository
from jobflow_desktop_app.search.state.search_progress_state import (
    SearchProgress,
    SearchStats,
)

try:
    from ._helpers import (
        FakeJobSearchRunner,
        create_candidate,
        create_profile,
        get_qapp,
        make_job,
        make_temp_context,
        process_events,
        suppress_message_boxes,
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
        suppress_message_boxes,
    )


class _RunningThread(QThread):
    def __init__(self) -> None:
        super().__init__()
        self._release = threading.Event()

    def run(self) -> None:  # pragma: no cover - wait loop
        self._release.wait(10)

    def release(self) -> None:
        self._release.set()


class _FakeRuntimeMirror:
    def __init__(self, companies: list[dict]) -> None:
        self._companies = [dict(item) for item in companies]

    def load_candidate_company_pool(self, *, candidate_id: int) -> list[dict]:
        del candidate_id
        return [dict(item) for item in self._companies]


class SearchResultsRegressionTests(unittest.TestCase):
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

    def _make_candidate_bundle(
        self,
        context,
        *,
        candidate_name: str,
        profile_name: str,
        scope_profile: str = "adjacent_mbse",
    ) -> int:
        candidate_id = create_candidate(context, name=candidate_name)
        create_profile(
            context,
            candidate_id,
            name=profile_name,
            scope_profile=scope_profile,
            is_active=True,
        )
        return candidate_id

    def test_pending_resume_text_and_stats_stay_aligned_after_reload(self) -> None:
        with make_temp_context() as context:
            save_runner = FakeJobSearchRunner()
            save_runner.set_progress(
                SearchProgress(
                    status="running",
                    stage="resume",
                    message="resume pending jobs",
                    last_event="resume",
                    elapsed_seconds=95,
                )
            )
            save_runner.set_jobs(
                [
                    make_job(title="Systems Engineer", date_found="2026-04-14T12:00:00Z"),
                    make_job(title="Validation Engineer", date_found="2026-04-13T12:00:00Z"),
                ],
                stats=SearchStats(
                    candidate_company_pool_count=8,
                    main_discovered_job_count=6,
                    main_scored_job_count=5,
                    main_pending_analysis_count=1,
                ),
            )
            candidate_id = self._make_candidate_bundle(
                context,
                candidate_name="Demo Candidate",
                profile_name="Systems Engineer",
            )

            step = self._make_step(context, save_runner)
            step.set_candidate(context.candidates.get(candidate_id))
            process_events()

            self.assertEqual(step.table.rowCount(), 2)
            self.assertIn("另有 1 条上次未补完的岗位", step.results_meta_label.text())
            self.assertIn("本轮找到 6 条", step.results_progress_label.text())
            self.assertIn("建议现在继续搜索", step.results_progress_label.text())
            self.assertIn("已评分 5 条", step.results_stats_label.text())
            self.assertIn("待补完 1 条", step.results_stats_label.text())
            self.assertIn("补完待处理岗位", step._search_progress_text(candidate_id)[1])
            self.assertNotIn("已运行", step._search_progress_text(candidate_id)[1])

    def test_internal_stats_show_latest_company_diagnosis_when_available(self) -> None:
        with make_temp_context() as context:
            fake_runner = FakeJobSearchRunner()
            fake_runner.runtime_mirror = _FakeRuntimeMirror(
                [
                    {
                        "name": "Lokalise",
                        "lastSearchedAt": "2026-04-18T10:00:00Z",
                        "sourceDiagnostics": {
                            "reason": "all_jobs_filtered",
                            "sourcePath": "company_search",
                            "rawJobsFetched": 3,
                            "snapshotJobs": 0,
                            "selectedJobs": 0,
                            "queuedJobs": 0,
                        },
                    }
                ]
            )
            fake_runner.set_jobs(
                [],
                stats=SearchStats(
                    candidate_company_pool_count=1,
                    main_discovered_job_count=0,
                    main_scored_job_count=0,
                    main_pending_analysis_count=0,
                ),
            )
            candidate_id = self._make_candidate_bundle(
                context,
                candidate_name="Demo Candidate",
                profile_name="Localization Manager",
            )

            step = self._make_step(context, fake_runner)
            step.set_candidate(context.candidates.get(candidate_id))
            process_events()

            self.assertIn("最近公司诊断：", step.results_stats_label.text())
            self.assertIn("Lokalise", step.results_stats_label.text())
            self.assertIn("抓到了岗位，但全部被过滤", step.results_stats_label.text())

    def test_running_search_on_one_candidate_keeps_other_candidate_read_only(self) -> None:
        with make_temp_context() as context:
            fake_runner = FakeJobSearchRunner()
            candidate_a = self._make_candidate_bundle(
                context,
                candidate_name="Candidate A",
                profile_name="Systems Engineer",
            )
            candidate_b = self._make_candidate_bundle(
                context,
                candidate_name="Candidate B",
                profile_name="Validation Engineer",
            )

            fake_runner.set_jobs(
                [
                    make_job(title="Systems Engineer", date_found="2026-04-14T12:00:00Z"),
                    make_job(title="Validation Engineer", date_found="2026-04-13T12:00:00Z"),
                ],
                stats=SearchStats(
                    candidate_company_pool_count=4,
                    main_discovered_job_count=2,
                    main_scored_job_count=2,
                    main_pending_analysis_count=0,
                ),
            )

            step = self._make_step(context, fake_runner)
            running_thread = _RunningThread()
            self.addCleanup(running_thread.release)
            self.addCleanup(running_thread.wait, 2000)
            running_thread.start()
            step._busy_task_thread = running_thread  # type: ignore[attr-defined]
            step._search_session.owner_candidate_id = candidate_a  # type: ignore[attr-defined]
            step._search_session.phase = "running"  # type: ignore[attr-defined]
            step.set_candidate(context.candidates.get(candidate_b))
            process_events()

            self.assertEqual(step.current_candidate_id, candidate_b)
            self.assertFalse(step.refresh_button.isEnabled())
            self.assertFalse(step.search_duration_combo.isEnabled())
            self.assertEqual(step.refresh_button.text(), "开始搜索")
            self.assertIn("另一位求职者", step.results_progress_label.text())
            self.assertIn("Candidate A", step.results_progress_label.text())
            self.assertIn("#b42318", step.results_progress_label.styleSheet())

    def test_clearing_selected_candidate_while_search_runs_keeps_owner_and_phase(self) -> None:
        with make_temp_context() as context:
            fake_runner = FakeJobSearchRunner()
            candidate_id = self._make_candidate_bundle(
                context,
                candidate_name="Candidate A",
                profile_name="Systems Engineer",
            )

            step = self._make_step(context, fake_runner)
            running_thread = _RunningThread()
            self.addCleanup(running_thread.release)
            self.addCleanup(running_thread.wait, 2000)
            running_thread.start()
            step._busy_task_thread = running_thread  # type: ignore[attr-defined]
            step._search_session.owner_candidate_id = candidate_id  # type: ignore[attr-defined]
            step._search_session.phase = "running"  # type: ignore[attr-defined]
            step.set_candidate(context.candidates.get(candidate_id))
            process_events()

            step.set_candidate(None)
            process_events()

            self.assertIsNone(step.current_candidate_id)
            self.assertEqual(step._search_session.owner_candidate_id, candidate_id)  # type: ignore[attr-defined]
            self.assertEqual(step._search_session.phase, "running")  # type: ignore[attr-defined]
            self.assertTrue(step._is_search_running())  # type: ignore[attr-defined]
            self.assertFalse(step.refresh_button.isEnabled())
            self.assertFalse(step.search_duration_combo.isEnabled())
            self.assertIn("后台搜索仍在运行", step.results_meta_label.text())

    def test_hidden_jobs_stay_hidden_after_reload_and_stats_ignore_deleted_rows(self) -> None:
        with make_temp_context() as context:
            fake_runner = FakeJobSearchRunner()
            candidate_id = self._make_candidate_bundle(
                context,
                candidate_name="Demo Candidate",
                profile_name="Systems Engineer",
            )
            jobs = [
                make_job(
                    title="Newest Role",
                    company="Acme Robotics",
                    url="https://example.com/jobs/new",
                    date_found="2026-04-14T12:00:00Z",
                ),
                make_job(
                    title="Middle Role",
                    company="Acme Robotics",
                    url="https://example.com/jobs/mid",
                    date_found="2026-04-13T12:00:00Z",
                ),
                make_job(
                    title="Old Role",
                    company="Acme Robotics",
                    url="https://example.com/jobs/old",
                    date_found="2026-04-12T12:00:00Z",
                ),
            ]
            fake_runner.load_live_jobs = lambda _candidate_id: list(jobs)  # type: ignore[method-assign]
            fake_runner.set_jobs(
                jobs,
                stats=SearchStats(
                    candidate_company_pool_count=9,
                    main_discovered_job_count=3,
                    main_scored_job_count=2,
                    main_pending_analysis_count=1,
                ),
            )

            step = self._make_step(context, fake_runner)
            step.set_candidate(context.candidates.get(candidate_id))
            process_events()

            self.assertEqual(step.table.rowCount(), 3)
            self.assertEqual(step.table.item(0, 0).text(), "Newest Role")
            self.assertIn("主流程已发现 3 条", step.results_stats_label.text())
            self.assertIn("待补完 1 条", step.results_stats_label.text())

            step.table.selectRow(0)
            process_events()
            with suppress_message_boxes():
                QTest.mouseClick(step.delete_button, Qt.LeftButton)
            process_events()

            self.assertEqual(step.table.rowCount(), 2)
            statuses, hidden = JobReviewStateRepository(context.database).load_candidate_review_state(
                candidate_id
            )
            self.assertEqual(statuses, {})
            self.assertEqual(hidden, {"https://example.com/jobs/new"})

            reloaded_step = self._make_step(context, fake_runner)
            reloaded_step.set_candidate(context.candidates.get(candidate_id))
            process_events()

            self.assertEqual(reloaded_step.table.rowCount(), 2)
            self.assertEqual(reloaded_step.table.item(0, 0).text(), "Middle Role")
            self.assertEqual(reloaded_step.table.item(1, 0).text(), "Old Role")
            self.assertIn("主流程已发现 3 条", reloaded_step.results_stats_label.text())
            self.assertIn("已评分 2 条", reloaded_step.results_stats_label.text())
            self.assertIn("待补完 1 条", reloaded_step.results_stats_label.text())

    def test_progress_stage_labels_cover_resume_discover_finalize_done(self) -> None:
        with make_temp_context() as context:
            fake_runner = FakeJobSearchRunner()
            candidate_id = self._make_candidate_bundle(
                context,
                candidate_name="Demo Candidate",
                profile_name="Systems Engineer",
            )
            step = self._make_step(context, fake_runner)
            step.set_candidate(context.candidates.get(candidate_id))
            process_events()

            expected = {
                "resume": "补完待处理岗位",
                "discover": "公司池主流程",
                "finalize": "收尾补完岗位",
                "done": "已完成",
            }
            for stage, expected_text in expected.items():
                with self.subTest(stage=stage):
                    fake_runner.set_progress(
                SearchProgress(
                            status="running",
                            stage=stage,
                            message=stage,
                            last_event=stage,
                            elapsed_seconds=125,
                        )
                    )
                    detail_text, dialog_text = step._search_progress_text(candidate_id)
                    self.assertIn(expected_text, detail_text)
                    self.assertIn(expected_text, dialog_text or detail_text)
                    self.assertNotIn("已运行", detail_text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
