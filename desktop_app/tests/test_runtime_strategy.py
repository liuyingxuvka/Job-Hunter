from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.runtime_strategy import (
    compact_adaptive_search_config,
    derive_adaptive_runtime_strategy,
    session_pass_timeout_seconds,
)


class RuntimeStrategyTests(unittest.TestCase):
    def test_compact_adaptive_search_config_keeps_canonical_shape(self) -> None:
        payload = {
            "passWorkBudgetSeconds": 150,
            "companyBatchSize": 7,
            "discoveryBreadth": 5,
            "cooldownBaseDays": 9,
        }
        compact_adaptive_search_config(payload)
        self.assertEqual(
            payload,
            {
                "passWorkBudgetSeconds": 150,
                "companyBatchSize": 7,
                "discoveryBreadth": 5,
                "cooldownBaseDays": 9,
            },
        )

    def test_compact_adaptive_search_config_migrates_legacy_batch_aliases(self) -> None:
        payload = {
            "existingPoolBatchSize": 3,
            "followThroughBatchSize": 4,
        }

        compact_adaptive_search_config(payload)

        self.assertEqual(
            payload,
            {
                "passWorkBudgetSeconds": 120,
                "companyBatchSize": 7,
                "discoveryBreadth": 4,
                "cooldownBaseDays": 7,
            },
        )

    def test_derive_adaptive_runtime_strategy_matches_desktop_defaults(self) -> None:
        strategy = derive_adaptive_runtime_strategy(
            {
                "passWorkBudgetSeconds": 120,
                "companyBatchSize": 4,
                "discoveryBreadth": 4,
                "cooldownBaseDays": 7,
            }
        )
        self.assertEqual(strategy["max_companies_per_run"], 4)
        self.assertEqual(strategy["max_new_companies_per_run"], 4)
        self.assertEqual(strategy["max_jobs_per_company"], 6)
        self.assertEqual(strategy["analysis_work_cap"], 24)
        self.assertEqual(strategy["company_rotation_interval_days"], 2)
        self.assertEqual(strategy["max_jobs_per_query"], 10)

    def test_session_pass_timeout_has_minimum_floor(self) -> None:
        self.assertEqual(session_pass_timeout_seconds({"passWorkBudgetSeconds": 30}), 60)
        self.assertEqual(session_pass_timeout_seconds({"passWorkBudgetSeconds": 180}), 180)


if __name__ == "__main__":
    unittest.main()

