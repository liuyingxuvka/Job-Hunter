from __future__ import annotations

import unittest

from PySide6.QtWidgets import QComboBox, QLabel, QTableWidget

from jobflow_desktop_app.app.pages import search_results_row_rendering

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
            display_target_role=lambda current_job: "Systems Engineer",
            format_score=lambda current_job: "88 / 100（高推荐）",
            make_link_cell=_make_link_cell,
            status_display=lambda code: {"pending": "待定", "focus": "重点"}[code],
            decorate_status_combo_items=lambda combo: None,
            apply_status_combo_style=lambda combo, code: combo.setProperty("statusCode", code),
            on_status_changed=lambda key, text, combo=None: changed.append((key, text)),
        )

        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), "Systems Engineer")
        self.assertEqual(table.item(0, 0).data(0x0100), "job-1")
        self.assertEqual(table.item(0, 2).text(), "Acme Robotics")
        self.assertEqual(table.item(0, 6).text(), "88 / 100（高推荐）")
        self.assertIsInstance(table.cellWidget(0, 4), QLabel)
        status_combo = table.cellWidget(0, 7)
        self.assertIsInstance(status_combo, QComboBox)
        self.assertEqual(status_combo.currentText(), "重点")
        self.assertEqual(status_combo.property("statusCode"), "focus")
        status_combo.setCurrentIndex(0)
        self.assertIn(("job-1", "待定"), changed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
