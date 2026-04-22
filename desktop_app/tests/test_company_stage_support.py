from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.stages.company_stage_support import (  # noqa: E402
    build_company_sources_stage_artifacts,
)


class CompanyStageSupportTests(unittest.TestCase):
    def test_build_company_sources_stage_artifacts_reconciles_candidate_pool_and_jobs(self) -> None:
        master_companies = [
            {
                "name": "Acme Energy",
                "website": "https://acme.example",
                "careersDiscoveryCache": {
                    "website": "https://acme.example",
                    "jobsPageUrl": "https://acme.example/careers",
                    "pageType": "jobs_listing",
                    "careersUrl": "https://acme.example/careers",
                    "sampleJobUrls": ["https://boards.greenhouse.io/acme/jobs/1"],
                },
            },
            {
                "name": "Existing Workday",
                "website": "https://existing.example",
                "careersUrl": "https://company.myworkdayjobs.com/foo",
                "jobPageCoverage": {
                    "companySearchCache": {
                        "cache-key": {
                            "query": "site:company.myworkdayjobs.com Existing Workday careers jobs",
                            "jobs": [{"title": "Role", "url": "https://company.myworkdayjobs.com/foo/job/1"}],
                        }
                    }
                },
            },
        ]
        fetch_result = SimpleNamespace(
            jobs=[
                {
                    "title": "Fuel Cell Engineer",
                    "company": "Acme Energy",
                    "location": "Berlin",
                    "url": "https://boards.greenhouse.io/acme/jobs/1",
                    "canonicalUrl": "https://boards.greenhouse.io/acme/jobs/1",
                    "dateFound": "2026-04-16T10:00:00Z",
                    "analysis": {},
                }
            ],
            processed_companies=[
                {
                    "name": "Acme Energy",
                    "website": "https://acme.example",
                    "snapshotComplete": True,
                    "knownJobUrls": ["https://boards.greenhouse.io/acme/jobs/1"],
                    "careersDiscoveryCache": {
                        "website": "https://acme.example",
                        "jobsPageUrl": "https://acme.example/careers",
                        "pageType": "jobs_listing",
                        "careersUrl": "https://acme.example/careers",
                        "sampleJobUrls": ["https://boards.greenhouse.io/acme/jobs/1"],
                    },
                }
            ],
            remaining_companies=[
                {
                    "name": "Existing Workday",
                    "website": "https://existing.example",
                    "careersUrl": "https://company.myworkdayjobs.com/foo",
                    "jobPageCoverage": {
                        "companySearchCache": {
                            "cache-key": {
                                "query": "site:company.myworkdayjobs.com Existing Workday careers jobs",
                                "jobs": [{"title": "Role", "url": "https://company.myworkdayjobs.com/foo/job/1"}],
                            }
                        }
                    },
                }
            ],
        )

        artifacts = build_company_sources_stage_artifacts(
            master_companies=master_companies,
            existing_jobs=[],
            fetch_result=fetch_result,
            config={"sources": {}, "analysis": {}, "filters": {}},
        )

        self.assertEqual(len(artifacts.deferred_companies), 1)
        self.assertEqual(artifacts.deferred_companies[0]["name"], "Existing Workday")
        self.assertEqual(len(artifacts.all_jobs), 1)
        self.assertEqual(len(artifacts.found_jobs), 1)
        acme = next(
            company
            for company in artifacts.candidate_companies
            if company.get("name") == "Acme Energy"
        )
        existing = next(
            company
            for company in artifacts.candidate_companies
            if company.get("name") == "Existing Workday"
        )
        self.assertTrue(acme["snapshotComplete"])
        self.assertEqual(acme["knownJobUrls"], ["https://boards.greenhouse.io/acme/jobs/1"])
        self.assertEqual(acme["careersDiscoveryCache"]["jobsPageUrl"], "https://acme.example/careers")
        self.assertEqual(existing["careersUrl"], "https://company.myworkdayjobs.com/foo")
        self.assertIn("cache-key", existing["jobPageCoverage"]["companySearchCache"])


if __name__ == "__main__":
    unittest.main()
