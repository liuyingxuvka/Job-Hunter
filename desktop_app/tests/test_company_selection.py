from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.companies.selection import (  # noqa: E402
    select_companies_for_run,
)


class CompanySelectionTests(unittest.TestCase):
    def test_select_companies_for_run_prefers_pending_then_ai_fit(self) -> None:
        companies = [
            {
                "name": "Pending Co",
                "website": "https://pending.example",
                "snapshotPendingAnalysisCount": 2,
                "aiCompanyFitScore": 10,
            },
            {
                "name": "Best Fit",
                "website": "https://best.example",
                "aiCompanyFitScore": 92,
            },
            {
                "name": "Lower Fit",
                "website": "https://lower.example",
                "aiCompanyFitScore": 40,
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=3,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
        )

        self.assertEqual(
            [item["name"] for item in selection],
            ["Pending Co", "Best Fit", "Lower Fit"],
        )

    def test_select_companies_for_run_filters_cooldown_and_unresolved_ranking(self) -> None:
        companies = [
            {
                "name": "Cooling Co",
                "website": "https://cooling.example",
                "cooldownUntil": "2099-01-01T00:00:00+00:00",
                "aiCompanyFitScore": 95,
            },
            {
                "name": "Ranking Unresolved",
                "website": "https://pending.example",
            },
            {
                "name": "Ready Co",
                "website": "https://ready.example",
                "aiCompanyFitScore": 70,
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=3,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
        )

        self.assertEqual(selection, [])

    def test_select_companies_for_run_waits_for_unscored_companies_before_sources(self) -> None:
        companies = [
            {
                "name": "Ready Co",
                "website": "https://ready.example",
                "aiCompanyFitScore": 70,
            },
            {
                "name": "New Specialist",
                "website": "https://specialist.example",
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
        )

        self.assertEqual(selection, [])

    def test_select_companies_for_run_filters_low_ai_fit_without_pending_work(self) -> None:
        companies = [
            {
                "name": "High Score Noise",
                "website": "https://noise.example",
                "aiCompanyFitScore": 28,
            },
            {
                "name": "Relevant Employer",
                "website": "https://relevant.example",
                "aiCompanyFitScore": 61,
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
        )

        self.assertEqual(
            [item["name"] for item in selection],
            ["Relevant Employer"],
        )

    def test_select_companies_for_run_honors_max_companies_without_rotation(self) -> None:
        companies = [
            {"name": "Fit A", "website": "https://a.example", "aiCompanyFitScore": 91},
            {"name": "Fit B", "website": "https://b.example", "aiCompanyFitScore": 70},
            {"name": "Fit C", "website": "https://c.example", "aiCompanyFitScore": 45},
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
        )

        self.assertEqual([item["name"] for item in selection], ["Fit A", "Fit B"])

    def test_select_companies_for_run_does_not_prioritize_transient_source_retry(self) -> None:
        companies = [
            {
                "name": "Transient Retry",
                "website": "https://retry.example",
                "aiCompanyFitScore": 95,
                "snapshotComplete": False,
                "sourceDiagnostics": {"reason": "transient_fetch_error"},
                "jobPageCoverage": {"coverageComplete": True, "pendingListingUrls": []},
                "lastSearchedAt": "2026-04-20T10:00:00+00:00",
            },
            {
                "name": "Roblox",
                "website": "https://roblox.example",
                "aiCompanyFitScore": 90,
            },
            {
                "name": "Smartling",
                "website": "https://smartling.example",
                "aiCompanyFitScore": 85,
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
        )

        self.assertEqual([item["name"] for item in selection], ["Roblox", "Smartling"])

    def test_select_companies_for_run_prioritizes_listing_frontier_work(self) -> None:
        companies = [
            {
                "name": "Frontier Pending",
                "website": "https://frontier.example",
                "aiCompanyFitScore": 55,
                "snapshotComplete": False,
                "sourceDiagnostics": {"reason": "listing_frontier_pending"},
                "jobPageCoverage": {
                    "coverageComplete": False,
                    "pendingListingUrls": ["https://frontier.example/jobs?page=2"],
                },
            },
            {
                "name": "High Fit Fresh",
                "website": "https://fresh.example",
                "aiCompanyFitScore": 90,
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
        )

        self.assertEqual([item["name"] for item in selection], ["Frontier Pending", "High Fit Fresh"])

    def test_select_companies_for_run_prioritizes_prerank_retry_before_fresh_company(self) -> None:
        companies = [
            {
                "name": "Prerank Retry",
                "website": "https://retry.example",
                "aiCompanyFitScore": 60,
                "sourceWorkState": {
                    "technicalFailureCount": 1,
                    "abandoned": False,
                    "lastFailureReason": "ai_job_prerank_pending_retry",
                },
                "jobPageCoverage": {"coverageComplete": True, "pendingListingUrls": []},
                "lastSearchedAt": "2026-04-20T10:00:00+00:00",
            },
            {
                "name": "Fresh Employer",
                "website": "https://fresh.example",
                "aiCompanyFitScore": 90,
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
        )

        self.assertEqual([item["name"] for item in selection], ["Prerank Retry", "Fresh Employer"])

    def test_select_companies_for_run_prefers_untouched_companies_before_completed_retries(self) -> None:
        companies = [
            {
                "name": "Retried Employer",
                "website": "https://retried.example",
                "aiCompanyFitScore": 94,
                "snapshotComplete": True,
                "sourceDiagnostics": {"reason": "no_jobs_fetched"},
                "lastSearchedAt": "2026-04-20T09:00:00+00:00",
            },
            {
                "name": "New Specialized Employer",
                "website": "https://specialized.example",
                "aiCompanyFitScore": 82,
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
        )

        self.assertEqual(
            [item["name"] for item in selection],
            ["New Specialized Employer", "Retried Employer"],
        )

    def test_select_companies_for_run_prefers_higher_fit_before_jobs_entry_hint(self) -> None:
        companies = [
            {
                "name": "Generic Giant",
                "website": "https://giant.example",
                "aiCompanyFitScore": 88,
            },
            {
                "name": "Specialized Employer",
                "website": "https://specialized.example",
                "aiCompanyFitScore": 80,
                "jobsPageUrl": "https://specialized.example/jobs",
                "jobsPageType": "jobs_listing",
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
        )

        self.assertEqual(
            [item["name"] for item in selection],
            ["Generic Giant", "Specialized Employer"],
        )

    def test_select_companies_for_run_skips_abandoned_source_units(self) -> None:
        companies = [
            {
                "name": "Broken Employer",
                "website": "https://broken.example",
                "aiCompanyFitScore": 95,
                "sourceWorkState": {
                    "technicalFailureCount": 3,
                    "abandoned": True,
                },
            },
            {
                "name": "Healthy Employer",
                "website": "https://healthy.example",
                "aiCompanyFitScore": 70,
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
        )

        self.assertEqual([item["name"] for item in selection], ["Healthy Employer"])

    def test_select_companies_for_run_skips_companies_suspended_for_current_run(self) -> None:
        companies = [
            {
                "name": "Deferred Employer",
                "website": "https://deferred.example",
                "aiCompanyFitScore": 85,
                "sourceWorkState": {
                    "technicalFailureCount": 0,
                    "abandoned": False,
                    "suspendedRunId": 99,
                    "lastFailureReason": "source_stage_deferred",
                },
            },
            {
                "name": "Healthy Employer",
                "website": "https://healthy.example",
                "aiCompanyFitScore": 70,
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
            current_run_id=99,
        )

        self.assertEqual([item["name"] for item in selection], ["Healthy Employer"])

    def test_select_companies_for_run_does_not_block_on_current_run_company_fit_failure(self) -> None:
        companies = [
            {
                "name": "Ranking Failed",
                "website": "https://failed.example",
                "rankingWorkState": {
                    "technicalFailureCount": 1,
                    "abandoned": False,
                    "suspendedRunId": 77,
                    "lastFailureReason": "company_fit_error",
                },
            },
            {
                "name": "Ready Employer",
                "website": "https://ready.example",
                "aiCompanyFitScore": 80,
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
            current_run_id=77,
        )

        self.assertEqual([item["name"] for item in selection], ["Ready Employer"])

    def test_select_companies_for_run_keeps_cached_score_company_after_refresh_failure(self) -> None:
        companies = [
            {
                "name": "Cached Employer",
                "website": "https://cached.example",
                "aiCompanyFitScore": 85,
                "rankingWorkState": {
                    "technicalFailureCount": 0,
                    "abandoned": False,
                    "suspendedRunId": 77,
                    "lastFailureReason": "company_fit_refresh_error",
                },
            },
            {
                "name": "Ready Employer",
                "website": "https://ready.example",
                "aiCompanyFitScore": 80,
            },
        ]

        selection = select_companies_for_run(
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 19, tzinfo=timezone.utc),
            current_run_id=77,
        )

        self.assertEqual([item["name"] for item in selection], ["Cached Employer", "Ready Employer"])


if __name__ == "__main__":
    unittest.main()
