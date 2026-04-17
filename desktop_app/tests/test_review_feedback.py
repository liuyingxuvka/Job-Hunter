from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.analysis.feedback import (  # noqa: E402
    TRACK_KEYS,
    build_feedback_rows_from_review_state,
    classify_feedback_row,
    compute_track_feedback_stats,
    job_review_key,
    load_review_state_snapshot,
    normalize_feedback_row,
    normalize_review_status_code,
)


class ReviewFeedbackTests(unittest.TestCase):
    def _job(
        self,
        *,
        url: str = "https://acme.example.com/jobs/123",
        title: str = "Fuel Cell Reliability Engineer",
        company: str = "Acme Hydrogen",
        date_found: str = "2026-04-14T12:00:00Z",
        fit_track: str = "hydrogen_core",
    ) -> dict:
        return {
            "url": url,
            "title": title,
            "company": company,
            "dateFound": date_found,
            "analysis": {
                "fitTrack": fit_track,
            },
        }

    def test_job_review_key_prefers_url_and_falls_back_to_identity(self) -> None:
        by_url = self._job(url="HTTPS://ACME.EXAMPLE.COM/JOBS/123")
        self.assertEqual(job_review_key(by_url), "https://acme.example.com/jobs/123")

        by_identity = self._job(url="")
        self.assertEqual(
            job_review_key(by_identity),
            "fuel cell reliability engineer|acme hydrogen|2026-04-14t12:00:00z",
        )

    def test_normalize_review_status_code_accepts_chinese_and_english(self) -> None:
        self.assertEqual(normalize_review_status_code("已投递"), "applied")
        self.assertEqual(normalize_review_status_code("Offer Received"), "offered")
        self.assertEqual(normalize_review_status_code("focus"), "focus")
        self.assertIsNone(normalize_review_status_code("unknown"))

    def test_normalize_feedback_row_falls_back_to_default_track(self) -> None:
        normalized = normalize_feedback_row(
            {
                "fitTrack": "nonexistent_track",
                "interest": " 感兴趣 ",
                "appliedCn": " 已投递 ",
            }
        )
        self.assertEqual(normalized["fitTrack"], "hydrogen_core")
        self.assertEqual(normalized["interest"], "感兴趣")
        self.assertEqual(normalized["appliedCn"], "已投递")

    def test_classify_feedback_row_can_be_both_positive_and_negative(self) -> None:
        result = classify_feedback_row(
            {
                "fitTrack": "hydrogen_core",
                "interest": "感兴趣",
                "responseStatus": "已拒",
            }
        )
        self.assertTrue(result["positive"])
        self.assertTrue(result["negative"])

    def test_build_feedback_rows_from_review_state_normalizes_keys(self) -> None:
        job = self._job(url="HTTPS://ACME.EXAMPLE.COM/JOBS/123", fit_track="battery_ess_powertrain")
        rows = build_feedback_rows_from_review_state(
            [job],
            statuses_by_job_key={"https://acme.example.com/jobs/123": "applied"},
            hidden_job_keys=["https://ACME.EXAMPLE.COM/jobs/123"],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fitTrack"], "battery_ess_powertrain")
        self.assertEqual(rows[0]["appliedCn"], "已投递")
        self.assertEqual(rows[0]["hidden"], "是")

    def test_compute_track_feedback_stats_aggregates_per_track(self) -> None:
        rows = [
            {
                "fitTrack": "hydrogen_core",
                "interest": "感兴趣",
            },
            {
                "fitTrack": "hydrogen_core",
                "notInterested": "是",
            },
            {
                "fitTrack": "battery_ess_powertrain",
                "responseStatus": "Offer",
            },
        ]
        stats = compute_track_feedback_stats(rows)
        self.assertEqual(stats["hydrogen_core"], {"positive": 1, "negative": 1})
        self.assertEqual(stats["battery_ess_powertrain"], {"positive": 1, "negative": 0})
        self.assertEqual(stats["energy_digitalization"], {"positive": 0, "negative": 0})

    def test_load_review_state_snapshot_handles_missing_invalid_and_valid_payloads(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            missing = load_review_state_snapshot(base / "missing.json")
            self.assertEqual(missing, {"statuses": {}, "hiddenJobKeys": []})

            broken_path = base / "broken.json"
            broken_path.write_text("{not-json", encoding="utf-8")
            broken = load_review_state_snapshot(broken_path)
            self.assertEqual(broken, {"statuses": {}, "hiddenJobKeys": []})

            valid_path = base / "valid.json"
            valid_path.write_text(
                json.dumps(
                    {
                        "generatedAt": "2026-04-14T12:00:00Z",
                        "candidateId": 7,
                        "statuses": {"https://acme.example.com/jobs/1": "focus"},
                        "hiddenJobKeys": ["https://acme.example.com/jobs/2"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            snapshot = load_review_state_snapshot(valid_path)
            self.assertEqual(snapshot["candidateId"], 7)
            self.assertEqual(
                snapshot["statuses"],
                {"https://acme.example.com/jobs/1": "focus"},
            )
            self.assertEqual(
                snapshot["hiddenJobKeys"],
                ["https://acme.example.com/jobs/2"],
            )


if __name__ == "__main__":
    unittest.main()

