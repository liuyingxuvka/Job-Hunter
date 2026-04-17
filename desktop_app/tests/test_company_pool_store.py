from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.companies.pool_store import (  # noqa: E402
    merge_companies_into_master,
    merge_company_runtime_state,
)
from jobflow_desktop_app.search.companies.selection import company_record_key  # noqa: E402


class CompanyPoolStoreTests(unittest.TestCase):
    def test_merge_company_runtime_state_preserves_runtime_fields(self) -> None:
        merged = merge_company_runtime_state(
            {
                "name": "Acme Hydrogen",
                "website": "https://acme.example",
                "knownJobUrls": ["https://example.com/a"],
                "sourceEvidence": {"webSearch": {"query": "old"}},
                "jobLinkCoverage": {"recentSeenJobUrls": ["https://example.com/a"], "cursor": 1},
            },
            {
                "name": "Acme Hydrogen",
                "website": "https://acme.example",
                "knownJobUrls": ["https://example.com/b"],
                "snapshotComplete": True,
                "sourceEvidence": {"webSearch": {"query": "new"}},
                "jobLinkCoverage": {"recentSeenJobUrls": ["https://example.com/b"], "cursor": 3},
            },
        )

        self.assertTrue(merged["snapshotComplete"])
        self.assertEqual(
            merged["knownJobUrls"],
            ["https://example.com/a", "https://example.com/b"],
        )
        self.assertEqual(merged["jobLinkCoverage"]["cursor"], 3)
        self.assertEqual(merged["sourceEvidence"]["webSearch"]["query"], "new")

    def test_merge_companies_into_master_updates_existing_record(self) -> None:
        merged_companies, changed = merge_companies_into_master(
            [{"name": "Acme Hydrogen", "website": "https://acme.example"}],
            [
                {
                    "name": "Acme Hydrogen",
                    "website": "https://acme.example",
                    "snapshotComplete": True,
                    "knownJobUrls": ["https://example.com/a"],
                }
            ],
        )

        self.assertEqual(changed, 1)
        company = merged_companies[0]
        self.assertEqual(company_record_key(company), "domain:acme.example")
        self.assertTrue(company["snapshotComplete"])
        self.assertEqual(company["knownJobUrls"], ["https://example.com/a"])

    def test_merge_companies_into_master_keeps_boolean_state_monotonic(self) -> None:
        merged_companies, changed = merge_companies_into_master(
            [
                {
                    "name": "Acme Hydrogen",
                    "website": "https://acme.example",
                    "snapshotComplete": True,
                }
            ],
            [
                {
                    "name": "Acme Hydrogen",
                    "website": "https://acme.example",
                    "snapshotComplete": False,
                    "knownJobUrls": ["https://example.com/a"],
                }
            ],
        )

        self.assertEqual(changed, 1)
        company = merged_companies[0]
        self.assertTrue(company["snapshotComplete"])
        self.assertEqual(company["knownJobUrls"], ["https://example.com/a"])

    def test_merge_companies_into_master_appends_new_company(self) -> None:
        merged_companies, changed = merge_companies_into_master(
            [{"name": "Acme Hydrogen", "website": "https://acme.example"}],
            [{"name": "Beta Power", "website": "https://beta.example"}],
        )

        self.assertEqual(changed, 1)
        self.assertEqual(len(merged_companies), 2)
        self.assertEqual(
            [company["name"] for company in merged_companies],
            ["Acme Hydrogen", "Beta Power"],
        )


if __name__ == "__main__":
    unittest.main()
