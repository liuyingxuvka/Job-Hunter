from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.companies.state import (  # noqa: E402
    get_company_cooldown_until,
    reconcile_company_pipeline_state_in_memory,
)


class CompanyStateTests(unittest.TestCase):
    def test_get_company_cooldown_until_uses_new_job_priority(self) -> None:
        now = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
        result = get_company_cooldown_until(
            {"cooldownBaseDays": 7},
            jobs_found_count=5,
            new_jobs_count=2,
            now=now,
        )
        self.assertEqual(result, "2026-04-17T12:00:00+00:00")

    def test_reconcile_company_pipeline_state_updates_pending_and_cooldown(self) -> None:
        companies = [
            {
                "name": "Acme Energy",
                "snapshotComplete": True,
                "snapshotJobUrls": ["https://example.com/jobs/a"],
                "knownJobUrls": ["https://example.com/jobs/a"],
                "lastJobsFoundCount": 1,
                "lastNewJobsCount": 0,
            },
            {
                "name": "Beta Systems",
                "snapshotComplete": True,
                "snapshotJobUrls": ["https://example.com/jobs/b"],
                "knownJobUrls": ["https://example.com/jobs/b"],
                "cooldownUntil": "2026-04-20T00:00:00+00:00",
            },
        ]
        jobs = [
            {
                "url": "https://example.com/jobs/a",
                "analysis": {},
            },
            {
                "url": "https://example.com/jobs/b",
                "analysis": {"overallScore": 78, "matchScore": 78},
            },
        ]

        result = reconcile_company_pipeline_state_in_memory(
            companies=companies,
            jobs=jobs,
            config={"adaptiveSearch": {"cooldownBaseDays": 7}},
            now=datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(result["changed"])
        self.assertEqual(result["pendingCompanies"], 1)
        acme = companies[0]
        beta = companies[1]
        self.assertEqual(acme["snapshotPendingAnalysisCount"], 1)
        self.assertNotIn("cooldownUntil", acme)
        self.assertNotIn("snapshotPendingAnalysisCount", beta)
        self.assertEqual(beta["cooldownUntil"], "2026-04-22T12:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
