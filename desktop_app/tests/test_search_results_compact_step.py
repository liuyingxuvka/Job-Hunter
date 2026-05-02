from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QWidget

from jobflow_desktop_app.app.pages.search_results import SearchResultsStep
from jobflow_desktop_app.app.pages.search_results_compact import SearchResultsCompactStep
from jobflow_desktop_app.db.repositories.search_runtime import JobReviewStateRepository
from jobflow_desktop_app.search.state.search_progress_state import SearchStats

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


class SearchResultsCompactStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    @staticmethod
    def _visible_headers(step) -> list[str]:
        header = step.table.horizontalHeader()
        return [
            step.table.horizontalHeaderItem(header.logicalIndex(visual_index)).text()
            for visual_index in range(step.table.columnCount())
        ]

    @staticmethod
    def _visible_headers_for_table(table) -> list[str]:
        header = table.horizontalHeader()
        return [
            table.horizontalHeaderItem(header.logicalIndex(visual_index)).text()
            for visual_index in range(table.columnCount())
        ]

    def test_compact_step_uses_compact_visible_order_but_preserves_shared_controls(self) -> None:
        with make_temp_context() as context:
            fake_runner = FakeJobSearchRunner()
            with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
                return_value=fake_runner,
            ):
                base = SearchResultsStep(context, ui_language="zh")
                compact = SearchResultsCompactStep(context, ui_language="zh")
            self.addCleanup(base.deleteLater)
            self.addCleanup(compact.deleteLater)

            base.show()
            compact.show()
            process_events()

            self.assertEqual(compact.table.columnCount(), base.table.columnCount() + 1)
            self.assertEqual(
                self._visible_headers(base),
                [
                    "职位名称",
                    "针对岗位",
                    "公司",
                    "地点",
                    "职位详情链接",
                    "发现时间",
                    "匹配性评分",
                    "状态",
                ],
            )
            self.assertEqual(
                self._visible_headers(compact),
                [
                    "",
                    "职位名称",
                    "评分",
                    "详情链接",
                    "状态",
                    "公司",
                    "地点",
                    "针对岗位",
                    "发现时间",
                ],
            )
            self.assertEqual(base.refresh_button.text(), compact.refresh_button.text())
            self.assertEqual(compact.delete_button.text(), "删除勾选岗位")
            toolbar = compact.findChild(QWidget, "CompactResultsToolbar")
            self.assertIsNotNone(toolbar)
            assert toolbar is not None
            self.assertTrue(toolbar.isAncestorOf(compact.refresh_button))
            self.assertTrue(toolbar.isAncestorOf(compact.delete_button))
            self.assertTrue(toolbar.isAncestorOf(compact.search_duration_combo))
            self.assertTrue(toolbar.isAncestorOf(compact.recycle_bin_button))
            control_card = compact.findChild(QWidget, "CompactResultsControlCard")
            self.assertIsNotNone(control_card)
            assert control_card is not None
            self.assertIs(control_card.layout().itemAt(0).widget(), toolbar)
            self.assertEqual(compact.table.columnWidth(1), 220)
            self.assertEqual(compact.table.columnWidth(7), 80)
            self.assertEqual(
                [compact.search_duration_combo.itemData(index) for index in range(compact.search_duration_combo.count())],
                [base.search_duration_combo.itemData(index) for index in range(base.search_duration_combo.count())],
            )
            self.assertEqual(
                [base.search_duration_combo.itemData(index) for index in range(base.search_duration_combo.count())],
                [3600, 7200, 10800, 14400],
            )
            self.assertEqual(
                [base.search_duration_combo.itemText(index) for index in range(base.search_duration_combo.count())],
                ["1 小时", "2 小时", "3 小时", "4 小时"],
            )
            self.assertEqual(base.search_duration_combo.currentData(), 3600)

    def test_compact_step_does_not_reset_user_resized_widths_on_rerender(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            fake_runner = FakeJobSearchRunner()
            fake_runner.set_jobs(
                [
                    make_job(
                        title="Role One",
                        company="Acme Robotics",
                        location="Berlin, Germany",
                        url="https://example.com/jobs/one",
                        date_found="2026-04-14T12:00:00Z",
                    ),
                    make_job(
                        title="Role Two",
                        company="Acme Robotics",
                        location="Berlin, Germany",
                        url="https://example.com/jobs/two",
                        date_found="2026-04-13T12:00:00Z",
                    ),
                ]
            )
            with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
                return_value=fake_runner,
            ):
                compact = SearchResultsCompactStep(context, ui_language="zh")
            self.addCleanup(compact.deleteLater)

            compact.show()
            compact.set_candidate(context.candidates.get(candidate_id))
            process_events()

            compact.table.setColumnWidth(1, 333)
            compact._reload_existing_results(candidate_id)
            process_events()

            self.assertEqual(compact.table.columnWidth(1), 333)

    def test_compact_step_preserves_scroll_on_live_rerender(self) -> None:
        with make_temp_context() as context:
            fake_runner = FakeJobSearchRunner()
            with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
                return_value=fake_runner,
            ):
                compact = SearchResultsCompactStep(context, ui_language="zh")
            self.addCleanup(compact.deleteLater)

            compact.resize(1200, 520)
            compact.show()
            jobs = [
                make_job(
                    title=f"Role {index:02d}",
                    company="Acme Robotics",
                    url=f"https://example.com/jobs/{index:02d}",
                    date_found=f"2026-04-{index + 1:02d}T12:00:00Z",
                )
                for index in range(30)
            ]
            compact._render_visible_jobs(jobs)
            process_events()

            vertical_bar = compact.table.verticalScrollBar()
            vertical_bar.setValue(vertical_bar.maximum())
            process_events()
            scrolled_value = vertical_bar.value()
            self.assertGreater(scrolled_value, 0)

            changed_jobs = list(jobs)
            changed_jobs[0] = make_job(
                title="Role 00 Updated",
                company="Acme Robotics",
                url="https://example.com/jobs/00",
                date_found="2026-04-01T12:00:00Z",
            )

            compact._render_visible_jobs(changed_jobs)
            process_events()

            self.assertEqual(vertical_bar.value(), min(scrolled_value, vertical_bar.maximum()))

    def test_compact_step_uses_shorter_score_found_time_and_link_display(self) -> None:
        with make_temp_context() as context:
            fake_runner = FakeJobSearchRunner()
            with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
                return_value=fake_runner,
            ):
                compact = SearchResultsCompactStep(context, ui_language="zh")
            self.addCleanup(compact.deleteLater)

            self.assertEqual(compact._format_score(type("Job", (), {"bound_target_role_score": 78, "match_score": 78})()), "78 中")
            self.assertEqual(compact._compact_found_time_text("2026-04-22T06:57:27+00:00"), "04-22 06:57")

            widget = compact._make_link_cell("https://jobs.zalando.com/en/noindex/example-role", False)
            self.assertEqual(widget.toolTip(), "https://jobs.zalando.com/en/noindex/example-role")
            self.assertIn("jobs.zalando.com", widget.text())
            self.assertNotIn("详情", widget.text())
            self.assertIn("background: #ffffff;", compact.table.horizontalHeader().viewport().styleSheet())
            self.assertIn("background: #ffffff;", compact.table.verticalHeader().viewport().styleSheet())

    def test_compact_step_prefers_localized_title_and_location_display(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Localized Candidate")
            create_profile(context, candidate_id, name="Localization Manager", is_active=True)
            fake_runner = FakeJobSearchRunner()
            fake_runner.set_jobs(
                [
                    make_job(
                        title="Lokalisierungsmanager",
                        location="Berlin, Deutschland",
                        title_zh="本地化经理",
                        location_zh="德国柏林",
                        url="https://example.com/jobs/localized",
                    ),
                ]
            )
            with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
                return_value=fake_runner,
            ):
                compact = SearchResultsCompactStep(context, ui_language="zh")
            self.addCleanup(compact.deleteLater)

            compact.set_candidate(context.candidates.get(candidate_id))
            compact.show()
            process_events()

            self.assertEqual(compact.table.item(0, 1).text(), "本地化经理")
            self.assertEqual(compact.table.item(0, 4).text(), "德国柏林")

    def test_compact_step_uses_short_status_and_single_line_stats(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            fake_runner = FakeJobSearchRunner()
            fake_runner.stats = SearchStats(
                candidate_company_pool_count=5,
                main_discovered_job_count=8,
                main_scored_job_count=8,
                recommended_job_count=2,
                main_pending_analysis_count=0,
            )
            with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
                return_value=fake_runner,
            ):
                compact = SearchResultsCompactStep(context, ui_language="zh")
            self.addCleanup(compact.deleteLater)

            compact.set_candidate(context.candidates.get(candidate_id))
            compact._set_results_main_status("当前求职者：Demo Candidate。已加载最近一次运行结果。")
            compact._refresh_results_stats_label()
            compact._set_results_progress_detail("后台进度：公司池主流程")
            process_events()

            self.assertEqual(compact.results_meta_label.text(), "已加载最近结果")
            self.assertEqual(compact.results_stats_label.text(), "公司池 5 · 发现 8 · 分析 8 · 最终推荐 2 · 待补完 0")
            self.assertEqual(compact.results_progress_label.text(), "公司池主流程")
            self.assertFalse(compact.results_meta_label.wordWrap())
            self.assertFalse(compact.results_stats_label.wordWrap())

    def test_compact_loaded_results_status_hides_completion_paragraph(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            fake_runner = FakeJobSearchRunner()
            fake_runner.stats = SearchStats(
                candidate_company_pool_count=5,
                main_discovered_job_count=8,
                main_scored_job_count=8,
                recommended_job_count=2,
                main_pending_analysis_count=0,
            )
            with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
                return_value=fake_runner,
            ):
                compact = SearchResultsCompactStep(context, ui_language="zh")
            self.addCleanup(compact.deleteLater)

            compact.set_candidate(context.candidates.get(candidate_id))
            compact._set_loaded_results_status(visible_count=8, pending_count=0)
            compact._refresh_results_stats_label()
            process_events()

            self.assertEqual(compact.results_meta_label.text(), "已加载最近结果")
            self.assertEqual(compact.results_progress_label.text(), "")
            self.assertFalse(compact.results_progress_label.isVisible())

    def test_compact_step_deletes_only_checked_rows_and_persists_hidden_state(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            fake_runner = FakeJobSearchRunner()
            fake_runner.set_jobs(
                [
                    make_job(
                        title="Newest Role",
                        company="Acme Robotics",
                        url="https://example.com/jobs/new",
                        date_found="2026-04-14T12:00:00Z",
                    ),
                    make_job(
                        title="Older Role",
                        company="Acme Robotics",
                        url="https://example.com/jobs/old",
                        date_found="2026-04-13T12:00:00Z",
                    ),
                ]
            )
            with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
                return_value=fake_runner,
            ):
                compact = SearchResultsCompactStep(context, ui_language="zh")
            self.addCleanup(compact.deleteLater)

            compact.set_candidate(context.candidates.get(candidate_id))
            compact.show()
            process_events()

            checkbox_cell = compact.table.cellWidget(0, 0)
            self.assertIsNotNone(checkbox_cell)
            checkbox = checkbox_cell.findChild(QPushButton)
            self.assertIsNotNone(checkbox)
            QTest.mouseClick(checkbox, Qt.LeftButton)
            process_events()

            with suppress_message_boxes():
                QTest.mouseClick(compact.delete_button, Qt.LeftButton)
            process_events()

            self.assertEqual(compact.table.rowCount(), 1)
            self.assertEqual(compact.table.item(0, 1).text(), "Older Role")
            statuses, hidden = JobReviewStateRepository(context.database).load_candidate_review_state(
                candidate_id
            )
            self.assertEqual(hidden, {"https://example.com/jobs/new"})
            self.assertEqual(compact._checked_delete_job_keys(), [])
            self.assertEqual(statuses, {})

    def test_compact_step_centers_text_items_and_uses_checkbox_widget_for_delete_column(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            fake_runner = FakeJobSearchRunner()
            fake_runner.set_jobs(
                [
                    make_job(
                        title="Centered Role",
                        company="Acme Robotics",
                        location="Berlin, Germany",
                        url="https://example.com/jobs/centered",
                        date_found="2026-04-14T12:00:00Z",
                        match_score=78,
                    ),
                ]
            )
            with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
                return_value=fake_runner,
            ):
                compact = SearchResultsCompactStep(context, ui_language="zh")
            self.addCleanup(compact.deleteLater)

            compact.set_candidate(context.candidates.get(candidate_id))
            compact.show()
            process_events()

            self.assertIsNone(compact.table.item(0, 0))
            checkbox_cell = compact.table.cellWidget(0, 0)
            self.assertIsNotNone(checkbox_cell)
            checkbox = checkbox_cell.findChild(QPushButton)
            self.assertIsNotNone(checkbox)
            self.assertEqual(checkbox.width(), 20)
            self.assertEqual(checkbox.height(), 20)
            self.assertEqual(compact.table.item(0, 1).textAlignment(), int(Qt.AlignCenter))
            self.assertEqual(compact.table.item(0, 7).textAlignment(), int(Qt.AlignCenter))

    def test_compact_recycle_bin_restores_hidden_jobs(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            fake_runner = FakeJobSearchRunner()
            fake_runner.set_jobs(
                [
                    make_job(
                        title="Newest Role",
                        company="Acme Robotics",
                        url="https://example.com/jobs/new",
                        date_found="2026-04-14T12:00:00Z",
                    ),
                    make_job(
                        title="Older Role",
                        company="Acme Robotics",
                        url="https://example.com/jobs/old",
                        date_found="2026-04-13T12:00:00Z",
                    ),
                ]
            )
            with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
                return_value=fake_runner,
            ):
                compact = SearchResultsCompactStep(context, ui_language="zh")
            self.addCleanup(compact.deleteLater)
            compact.set_candidate(context.candidates.get(candidate_id))
            compact.hidden_job_keys.add("https://example.com/jobs/new")
            compact._save_review_state()
            compact._reload_existing_results(candidate_id)
            process_events()

            self.assertEqual(compact.table.rowCount(), 1)
            deleted_jobs = compact._deleted_jobs_for_recycle_bin()
            self.assertEqual(
                [compact._job_key(item) for item in deleted_jobs],
                ["https://example.com/jobs/new"],
            )

            compact.hidden_job_keys.clear()
            compact._save_review_state()
            compact._reload_existing_results(candidate_id)
            process_events()

            self.assertEqual(compact.table.rowCount(), 2)

    def test_compact_recycle_bin_reuses_main_table_headers_and_widgets(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Recycle Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            fake_runner = FakeJobSearchRunner()
            fake_runner.set_jobs(
                [
                    make_job(
                        title="Newest Role",
                        company="Acme Robotics",
                        location="Berlin, Germany",
                        url="https://example.com/jobs/new",
                        date_found="2026-04-14T12:00:00Z",
                        match_score=78,
                    ),
                ]
            )
            with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
                return_value=fake_runner,
            ):
                compact = SearchResultsCompactStep(context, ui_language="zh")
            self.addCleanup(compact.deleteLater)

            compact.set_candidate(context.candidates.get(candidate_id))
            compact.hidden_job_keys.add("https://example.com/jobs/new")
            compact._save_review_state()
            compact._reload_existing_results(candidate_id)
            process_events()

            deleted_jobs = compact._deleted_jobs_for_recycle_bin()
            dialog, table, restore_selected_button, _restore_all_button = compact._build_recycle_bin_dialog(deleted_jobs)
            self.addCleanup(dialog.deleteLater)
            dialog.show()
            process_events()

            self.assertEqual(self._visible_headers(compact), self._visible_headers_for_table(table))
            self.assertIsNotNone(table.cellWidget(0, 0))
            self.assertIsInstance(table.cellWidget(0, 0).findChild(QPushButton), QPushButton)
            self.assertIsNotNone(table.cellWidget(0, 5))
            self.assertIsNotNone(table.cellWidget(0, 8))

            checkbox = table.cellWidget(0, 0).findChild(QPushButton)
            self.assertFalse(checkbox.isChecked())
            QTest.mouseClick(checkbox, Qt.LeftButton)
            process_events()
            self.assertTrue(checkbox.isChecked())

            QTest.mouseClick(restore_selected_button, Qt.LeftButton)
            process_events()
            self.assertNotIn("https://example.com/jobs/new", compact.hidden_job_keys)

    def test_compact_recycle_bin_restore_ignores_row_selection_without_checkbox(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Recycle Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            fake_runner = FakeJobSearchRunner()
            fake_runner.set_jobs(
                [
                    make_job(
                        title="Newest Role",
                        company="Acme Robotics",
                        url="https://example.com/jobs/new",
                        date_found="2026-04-14T12:00:00Z",
                    ),
                ]
            )
            with patch(
                "jobflow_desktop_app.app.pages.search_results.JobSearchRunner",
                return_value=fake_runner,
            ):
                compact = SearchResultsCompactStep(context, ui_language="zh")
            self.addCleanup(compact.deleteLater)

            compact.set_candidate(context.candidates.get(candidate_id))
            compact.hidden_job_keys.add("https://example.com/jobs/new")
            compact._save_review_state()
            compact._reload_existing_results(candidate_id)
            process_events()

            deleted_jobs = compact._deleted_jobs_for_recycle_bin()
            dialog, table, restore_selected_button, _restore_all_button = compact._build_recycle_bin_dialog(deleted_jobs)
            self.addCleanup(dialog.deleteLater)
            dialog.show()
            process_events()

            table.selectRow(0)
            process_events()
            QTest.mouseClick(restore_selected_button, Qt.LeftButton)
            process_events()

            self.assertIn("https://example.com/jobs/new", compact.hidden_job_keys)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
