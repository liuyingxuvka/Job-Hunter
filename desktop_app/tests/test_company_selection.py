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
    def _config(self) -> dict:
        return {
            "candidate": {
                "targetRole": "Hydrogen durability engineer",
                "locationPreference": "Berlin / Remote",
                "scopeProfile": "hydrogen_mainline",
            },
            "companyDiscovery": {
                "model": "gpt-5-nano",
                "queries": ["hydrogen companies", "electrolyzer durability companies"],
            },
            "sources": {
                "priorityRegionWeights": {"region:de": 8},
                "companyRotationIntervalDays": 2,
                "companyRotationSeed": 0,
                "maxCompaniesPerRun": 2,
            },
        }

    def test_select_companies_for_run_prefers_support_and_lifecycle(self) -> None:
        companies = [
            {"name": "Beta Storage", "website": "https://beta.example", "tags": ["region:DE"], "snapshotComplete": True},
            {"name": "Acme Hydrogen", "website": "https://acme.example", "tags": ["hydrogen", "region:DE"], "snapshotPendingAnalysisCount": 2},
            {"name": "Gamma Labs", "website": "https://gamma.example", "tags": ["battery"]},
        ]
        ordered = select_companies_for_run(
            config=self._config(),
            companies=companies,
            max_companies=10,
            now=datetime(2026, 4, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(ordered[0]["name"], "Acme Hydrogen")
        self.assertEqual(ordered[1]["name"], "Beta Storage")
        self.assertEqual(ordered[2]["name"], "Gamma Labs")

    def test_select_companies_for_run_rotates_tail(self) -> None:
        companies = [
            {"name": "Pending Company", "website": "https://pending.example", "snapshotPendingAnalysisCount": 2},
            {"name": "Incomplete Snapshot", "website": "https://incomplete.example", "snapshotComplete": False},
            {"name": "Company 2", "website": "https://c2.example"},
            {"name": "Company 3", "website": "https://c3.example"},
            {"name": "Company 4", "website": "https://c4.example"},
        ]
        selection = select_companies_for_run(
            config=self._config(),
            companies=companies,
            max_companies=3,
            now=datetime(2026, 4, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(len(selection), 3)
        self.assertEqual(
            [item["name"] for item in selection[:2]],
            ["Pending Company", "Incomplete Snapshot"],
        )

    def test_select_companies_for_run_rotation_can_be_disabled_with_zero_interval(self) -> None:
        config = self._config()
        config["sources"]["companyRotationIntervalDays"] = 0
        companies = [
            {"name": "Pending Company", "website": "https://pending.example", "snapshotPendingAnalysisCount": 2},
            {"name": "Incomplete Snapshot", "website": "https://incomplete.example", "snapshotComplete": False},
            {"name": "Stable Co A", "website": "https://stable-a.example"},
            {"name": "Stable Co B", "website": "https://stable-b.example"},
            {"name": "Stable Co C", "website": "https://stable-c.example"},
        ]
        selection = select_companies_for_run(
            config=config,
            companies=companies,
            max_companies=3,
            now=datetime(2026, 4, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(
            [item["name"] for item in selection],
            ["Pending Company", "Incomplete Snapshot", "Stable Co A"],
        )

    def test_select_companies_for_run_filters_cooldown_before_prioritizing(self) -> None:
        companies = [
            {"name": "Acme Hydrogen", "website": "https://acme.example", "tags": ["hydrogen", "region:DE"]},
            {"name": "Cooling Storage", "website": "https://beta.example", "tags": ["battery"], "cooldownUntil": "2099-01-01T00:00:00+00:00"},
        ]

        selection = select_companies_for_run(
            config=self._config(),
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(selection[0]["name"], "Acme Hydrogen")
        self.assertEqual([item["name"] for item in selection], ["Acme Hydrogen"])

    def test_select_companies_for_run_ignores_source_convenience_noise(self) -> None:
        companies = [
            {
                "name": "Plain Company",
                "website": "https://plain.example",
            },
            {
                "name": "Noisy Company",
                "website": "https://noisy.example",
                "careersUrl": "https://noisy.example/careers",
                "discoverySources": ["web_search"],
                "tags": ["hydrogen"],
            },
        ]

        ordered = select_companies_for_run(
            config=self._config(),
            companies=companies,
            max_companies=10,
            now=datetime(2026, 4, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(
            [item["name"] for item in ordered],
            ["Plain Company", "Noisy Company"],
        )

    def test_select_companies_for_run_prefers_manual_priority_and_region_bonus(self) -> None:
        companies = [
            {
                "name": "Plain Systems",
                "website": "https://plain.example",
                "priority": 0,
                "tags": [],
            },
            {
                "name": "Acme Hydrogen",
                "website": "https://acme.example",
                "priority": 3,
                "tags": ["region:de"],
            },
        ]
        selection = select_companies_for_run(
            config=self._config(),
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 15, tzinfo=timezone.utc),
        )
        self.assertEqual([item["name"] for item in selection], ["Acme Hydrogen", "Plain Systems"])

    def test_select_companies_for_run_pins_pending_before_rotating_tail(self) -> None:
        companies = [
            {"name": "Pending Co", "website": "https://pending.example", "snapshotPendingAnalysisCount": 2},
            {"name": "Incomplete Co", "website": "https://incomplete.example", "snapshotComplete": False},
            {"name": "Stable Co", "website": "https://stable.example", "priority": 2},
            {"name": "Stable Co 2", "website": "https://stable2.example"},
        ]
        selection = select_companies_for_run(
            config=self._config(),
            companies=companies,
            max_companies=3,
            now=datetime(2026, 4, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(
            [item["name"] for item in selection[:2]],
            ["Pending Co", "Incomplete Co"],
        )

    def test_select_companies_for_run_uses_rotation_seed_within_same_day(self) -> None:
        companies = [
            {"name": "Pending Co", "website": "https://pending.example", "snapshotPendingAnalysisCount": 1},
            {"name": "Stable Co 1", "website": "https://stable1.example"},
            {"name": "Stable Co 2", "website": "https://stable2.example"},
            {"name": "Stable Co 3", "website": "https://stable3.example"},
        ]
        config_a = self._config()
        config_b = self._config()
        config_a["sources"]["companyRotationSeed"] = 0
        config_b["sources"]["companyRotationSeed"] = 2

        selection_a = select_companies_for_run(
            config=config_a,
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 17, tzinfo=timezone.utc),
        )
        selection_b = select_companies_for_run(
            config=config_b,
            companies=companies,
            max_companies=2,
            now=datetime(2026, 4, 17, tzinfo=timezone.utc),
        )

        self.assertEqual(selection_a[0]["name"], "Pending Co")
        self.assertEqual(selection_b[0]["name"], "Pending Co")
        self.assertNotEqual(
            selection_a[1]["name"],
            selection_b[1]["name"],
        )

if __name__ == "__main__":
    unittest.main()

