from __future__ import annotations

import unittest

from jobflow_desktop_app.search.companies.source_diagnostics import (
    format_company_source_diagnostic_summary,
    select_recent_company_source_diagnostic_summary,
)


class CompanySourceDiagnosticsTests(unittest.TestCase):
    def test_format_company_source_diagnostic_summary_zh(self) -> None:
        summary = format_company_source_diagnostic_summary(
            {
                "name": "Lokalise",
                "sourceDiagnostics": {
                    "reason": "all_jobs_filtered",
                    "sourcePath": "company_page_followup",
                    "rawJobsFetched": 3,
                    "snapshotJobs": 0,
                    "selectedJobs": 0,
                    "queuedJobs": 0,
                    "followedBoardLinks": 1,
                },
            },
            ui_language="zh",
        )

        self.assertIn("Lokalise", summary)
        self.assertIn("抓到了岗位，但全部被过滤", summary)
        self.assertIn("来源=company_page_followup", summary)
        self.assertIn("抓取=3", summary)
        self.assertIn("跟进=1", summary)

    def test_select_recent_company_source_diagnostic_summary_prefers_latest_non_success(self) -> None:
        summary = select_recent_company_source_diagnostic_summary(
            [
                {
                    "name": "Queued Co",
                    "lastSearchedAt": "2026-04-18T10:00:00Z",
                    "sourceDiagnostics": {
                        "reason": "queued_jobs",
                        "sourcePath": "ats",
                        "rawJobsFetched": 10,
                        "snapshotJobs": 4,
                        "selectedJobs": 2,
                        "queuedJobs": 2,
                    },
                },
                {
                    "name": "Retry Co",
                    "lastSearchedAt": "2026-04-18T09:00:00Z",
                    "sourceDiagnostics": {
                        "reason": "transient_fetch_error",
                        "sourcePath": "company_page",
                        "rawJobsFetched": 0,
                        "snapshotJobs": 0,
                        "selectedJobs": 0,
                        "queuedJobs": 0,
                    },
                },
            ],
            ui_language="en",
        )

        self.assertIn("Retry Co", summary)
        self.assertIn("Transient lookup failure; retry later", summary)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
