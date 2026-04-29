from __future__ import annotations

import unittest

from PySide6.QtWidgets import QComboBox, QLabel, QTableWidget

from jobflow_desktop_app.app.pages import search_results_row_rendering
from jobflow_desktop_app.app.pages import search_results_review_status

try:
    from ._helpers import get_qapp, make_job
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import get_qapp, make_job  # type: ignore


class SearchResultsRowRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def test_populate_job_row_sets_cells_and_status_combo(self) -> None:
        table = QTableWidget(0, 8)
        self.addCleanup(table.deleteLater)
        job = make_job(
            title="Systems Engineer",
            company="Acme Robotics",
            location="Munich, Germany",
            date_found="2026-04-15T12:00:00Z",
        )
        changed: list[tuple[str, str]] = []

        def _make_link_cell(url: str, prefer_detail_label: bool) -> QLabel:
            return QLabel(f"{url}|{prefer_detail_label}")

        search_results_row_rendering.populate_job_row(
            table=table,
            row_index=0,
            job=job,
            job_key="job-1",
            detail_url="https://example.com/jobs/1",
            status_codes=("pending", "focus"),
            status_by_job_key={"job-1": "focus"},
            display_job_title=lambda current_job: "系统工程师",
            display_job_location=lambda current_job: "德国慕尼黑",
            display_target_role=lambda current_job: "Systems Engineer",
            format_score=lambda current_job: "88 / 100（高推荐）",
            make_link_cell=_make_link_cell,
            status_display=lambda code: {"pending": "待定", "focus": "重点"}[code],
            decorate_status_combo_items=lambda combo: None,
            apply_status_combo_style=lambda combo, code: combo.setProperty("statusCode", code),
            on_status_changed=lambda key, text, combo=None: changed.append((key, text)),
        )

        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), "系统工程师")
        self.assertEqual(table.item(0, 0).data(0x0100), "job-1")
        self.assertEqual(table.item(0, 2).text(), "Acme Robotics")
        self.assertEqual(table.item(0, 3).text(), "德国慕尼黑")
        self.assertEqual(table.item(0, 6).text(), "88 / 100（高推荐）")
        self.assertIsInstance(table.cellWidget(0, 4), QLabel)
        status_combo = table.cellWidget(0, 7)
        self.assertIsInstance(status_combo, QComboBox)
        self.assertEqual(status_combo.currentText(), "重点")
        self.assertEqual(status_combo.property("statusCode"), "focus")
        status_combo.setCurrentIndex(0)
        self.assertIn(("job-1", "待定"), changed)

    def test_status_palette_uses_white_pending_and_more_distinct_terminal_states(self) -> None:
        pending = search_results_review_status.status_palette("pending")
        rejected = search_results_review_status.status_palette("rejected")
        dropped = search_results_review_status.status_palette("dropped")

        self.assertEqual(pending["bg"], "#FFFFFF")
        self.assertEqual(pending["fg"], "#334155")
        self.assertNotEqual(rejected["bg"], dropped["bg"])
        self.assertNotEqual(rejected["border"], dropped["border"])

    def test_display_helpers_prefer_localized_job_fields_with_raw_fallback(self) -> None:
        from jobflow_desktop_app.app.pages import search_results_rendering

        job = make_job(
            title="Senior CRM Process Owner",
            location="Berlin, Germany",
            title_zh="高级 CRM 流程负责人",
            title_en="Senior CRM Process Owner",
            location_zh="德国柏林",
            location_en="Berlin, Germany",
        )

        self.assertEqual(
            search_results_rendering.display_job_title("zh", job),
            "高级 CRM 流程负责人",
        )
        self.assertEqual(
            search_results_rendering.display_job_location("zh", job),
            "德国柏林",
        )
        self.assertEqual(
            search_results_rendering.display_job_title("en", job),
            "Senior CRM Process Owner",
        )

    def test_display_target_role_marks_historical_rescore_status(self) -> None:
        from jobflow_desktop_app.app.pages import search_results_rendering

        job = make_job(
            bound_target_role_name_en="Fuel Cell Systems Engineer",
            current_target_role_status="needs_rescore",
        )

        self.assertEqual(
            search_results_rendering.display_target_role("zh", job),
            "Fuel Cell Systems Engineer（历史/待重算）",
        )
        self.assertEqual(
            search_results_rendering.display_target_role("en", job),
            "Fuel Cell Systems Engineer (historical / rescore)",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
