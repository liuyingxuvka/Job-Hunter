from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.output.manual_tracking import (
    has_manual_tracking,
    merge_manual_fields,
)


class ManualTrackingTests(unittest.TestCase):
    def test_has_manual_tracking_accepts_interest_marker(self) -> None:
        self.assertTrue(has_manual_tracking({"interest": "感兴趣"}))
        self.assertFalse(has_manual_tracking({"interest": ""}))

    def test_has_manual_tracking_accepts_other_manual_fields(self) -> None:
        self.assertTrue(has_manual_tracking({"notesCn": "follow up later"}))
        self.assertTrue(has_manual_tracking({"appliedDate": "2026-04-14"}))
        self.assertFalse(has_manual_tracking({}))

    def test_merge_manual_fields_preserves_existing_values(self) -> None:
        merged = merge_manual_fields(
            {
                "https://example.com/jobs/a": {
                    "interest": "感兴趣",
                    "notesCn": "first note",
                }
            },
            {
                "https://example.com/jobs/a": {
                    "appliedDate": "2026-04-14",
                },
                "https://example.com/jobs/b": {
                    "responseStatus": "已回复",
                },
            },
        )
        self.assertEqual(
            merged["https://example.com/jobs/a"],
            {
                "interest": "感兴趣",
                "appliedDate": "2026-04-14",
                "appliedCn": "",
                "responseStatus": "",
                "notInterested": "",
                "notesCn": "first note",
            },
        )
        self.assertEqual(merged["https://example.com/jobs/b"]["responseStatus"], "已回复")


if __name__ == "__main__":
    unittest.main()

