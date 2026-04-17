from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.output.manual_tracking_store import (  # noqa: E402
    collect_manual_fields_from_jobs,
    overlay_manual_fields_onto_jobs,
)


class ManualTrackingStoreTests(unittest.TestCase):
    def test_overlay_manual_fields_uses_alias_map(self) -> None:
        manual_by_alias = {
            "https://example.com/jobs/a": {
                "interest": "感兴趣",
                "appliedDate": "2026-04-15",
                "appliedCn": "已投递",
                "responseStatus": "已回复",
                "notInterested": "",
                "notesCn": "重点关注",
            }
        }
        jobs = overlay_manual_fields_onto_jobs(
            [
                {
                    "url": "https://example.com/jobs/a",
                    "title": "Fuel Cell Reliability Engineer",
                    "company": "Acme Energy",
                    "location": "Berlin, Germany",
                }
            ],
            manual_by_alias,
        )
        self.assertEqual(jobs[0]["interest"], "感兴趣")
        self.assertEqual(jobs[0]["notesCn"], "重点关注")

    def test_collect_manual_fields_from_jobs_builds_alias_map(self) -> None:
        jobs = [
            {
                "url": "https://example.com/jobs/a",
                "canonicalUrl": "https://example.com/jobs/a",
                "title": "Fuel Cell Reliability Engineer",
                "company": "Acme Energy",
                "location": "Berlin, Germany",
                "interest": "感兴趣",
                "notesCn": "重点关注",
            }
        ]
        collected = collect_manual_fields_from_jobs(jobs)
        self.assertIn("https://example.com/jobs/a", collected)
        self.assertIn(
            "acme energy|fuel cell reliability engineer|berlin germany",
            collected,
        )
        self.assertEqual(collected["https://example.com/jobs/a"]["interest"], "感兴趣")


if __name__ == "__main__":
    unittest.main()

