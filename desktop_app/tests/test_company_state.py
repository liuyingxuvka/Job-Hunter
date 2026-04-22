from __future__ import annotations

from datetime import datetime, timezone
import unittest

from jobflow_desktop_app.search.companies.state import (
    company_has_materialized_jobs_entry,
    get_company_cooldown_until,
    reconcile_company_pipeline_state_in_memory,
)


class CompanyStateTests(unittest.TestCase):
    def test_company_has_materialized_jobs_entry_checks_live_and_cached_sources(self) -> None:
        self.assertTrue(
            company_has_materialized_jobs_entry(
                {
                    "careersDiscoveryCache": {
                        "sampleJobUrls": ["https://example.com/jobs/1"],
                    }
                }
            )
        )
        self.assertFalse(company_has_materialized_jobs_entry({"careersDiscoveryCache": {}}))

    def test_reconcile_does_not_apply_cooldown_while_source_work_is_still_active(self) -> None:
        company = {
            "name": "Delivery Hero SE",
            "lastSearchedAt": "2026-04-21T20:28:22+00:00",
            "lastNewJobsCount": 0,
            "noNewJobCooldownStreak": 1,
            "cooldownUntil": "2026-04-28T20:28:22+00:00",
            "jobPageCoverage": {
                "visitedListingUrls": [],
                "pendingListingUrls": [],
                "coverageComplete": True,
            },
            "sourceWorkState": {
                "technicalFailureCount": 1,
                "abandoned": False,
                "suspendedRunId": 87,
                "lastFailureReason": "source_stage_deferred",
            },
        }

        result = reconcile_company_pipeline_state_in_memory(
            companies=[company],
            jobs=[],
            config={"adaptiveSearch": {"cooldownBaseDays": 7}},
        )

        self.assertTrue(result["changed"])
        self.assertNotIn("cooldownUntil", company)

    def test_reconcile_applies_cooldown_once_company_source_coverage_is_complete(self) -> None:
        now = datetime(2026, 4, 21, 20, 28, 22, tzinfo=timezone.utc)
        company = {
            "name": "Zalando SE",
            "lastSearchedAt": "2026-04-21T20:28:22+00:00",
            "lastNewJobsCount": 0,
            "noNewJobCooldownStreak": 0,
            "jobPageCoverage": {
                "visitedListingUrls": ["https://jobs.zalando.com/en/jobs"],
                "pendingListingUrls": [],
                "coverageComplete": True,
            },
        }

        reconcile_company_pipeline_state_in_memory(
            companies=[company],
            jobs=[],
            config={"adaptiveSearch": {"cooldownBaseDays": 7}},
            now=now,
        )

        self.assertEqual(
            company.get("cooldownUntil"),
            get_company_cooldown_until(
                {"cooldownBaseDays": 7},
                jobs_found_count=0,
                new_jobs_count=0,
                no_new_job_streak=1,
                now=now,
            ),
        )


if __name__ == "__main__":
    unittest.main()
