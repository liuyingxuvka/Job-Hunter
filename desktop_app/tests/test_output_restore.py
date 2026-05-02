from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.output.manual_tracking import has_manual_tracking
from jobflow_desktop_app.search.output.output_restore import (
    merge_recommended_jobs_append_mode,
    sort_jobs_for_append_merge,
)
from jobflow_desktop_app.search.analysis.scoring_contract import (
    passes_unified_recommendation_threshold,
)


class OutputRestoreTests(unittest.TestCase):
    @staticmethod
    def _job(
        title: str,
        score: int,
        *,
        key: str | None = None,
        date_found: str = "",
        recommend: bool = True,
        valid: bool = True,
    ) -> dict:
        resolved_key = key or title
        return {
            "title": title,
            "url": f"https://example.com/{resolved_key}",
            "outputKey": resolved_key,
            "dateFound": date_found,
            "analysis": {
                "recommend": recommend,
                "overallScore": score,
                "matchScore": score,
                "valid": valid,
            },
        }

    def test_sort_jobs_for_append_merge_prefers_higher_score_then_newer_date(self) -> None:
        jobs = [
            self._job("Older Strong", 80, date_found="2026-04-10"),
            self._job("Newer Strong", 80, date_found="2026-04-11"),
            self._job("Medium", 60, date_found="2026-04-12"),
        ]
        ordered = sort_jobs_for_append_merge(jobs)
        self.assertEqual(
            [job["title"] for job in ordered],
            ["Newer Strong", "Older Strong", "Medium"],
        )

    def test_append_merge_prunes_recent_invalid_untracked_rows(self) -> None:
        row = {
            "outputKey": "recent-invalid",
            "dateFound": "2026-04-14",
        }
        row_to_job = lambda row: self._job("Recent Invalid", 65, key=row["outputKey"], date_found=row["dateFound"], valid=False)
        key_for_job = lambda job: str(job.get("outputKey") or "")
        passes_final_output_check = lambda job: bool((job.get("analysis") or {}).get("valid"))
        prefers_candidate = lambda existing, candidate: False

        result = merge_recommended_jobs_append_mode(
            existing_rows=[row],
            all_jobs_by_key={
                "recent-invalid": self._job(
                    "Recent Invalid",
                    65,
                    key="recent-invalid",
                    date_found="2026-04-14",
                    valid=False,
                )
            },
            historical_jobs=[],
            new_jobs=[],
            tracker_now="2026-04-14T12:00:00Z",
            tracker_today="2026-04-14",
            row_to_job=row_to_job,
            key_for_job=key_for_job,
            passes_unified_threshold=passes_unified_recommendation_threshold,
            passes_final_output_check=passes_final_output_check,
            has_manual_tracking=has_manual_tracking,
            prefers_candidate_over_existing=prefers_candidate,
        )
        self.assertEqual(result.pruned_recent_invalid_rows, 1)
        self.assertEqual(result.jobs, [])

    def test_append_merge_keeps_manual_rows_restores_history_and_prefers_new_better_job(self) -> None:
        existing_row = {
            "outputKey": "shared-job",
            "dateFound": "2026-04-14",
            "notesCn": "keep this row",
        }
        row_to_job = lambda row: self._job(
            "Existing Job",
            65,
            key=row["outputKey"],
            date_found=row["dateFound"],
            valid=False,
        )
        key_for_job = lambda job: str(job.get("outputKey") or "")
        passes_final_output_check = lambda job: bool((job.get("analysis") or {}).get("valid"))
        prefers_candidate = (
            lambda existing, candidate: int((candidate.get("analysis") or {}).get("overallScore", -1))
            > int((existing.get("analysis") or {}).get("overallScore", -1))
        )

        historical_job = self._job(
            "Historical Valid",
            70,
            key="historical-job",
            date_found="2026-04-01",
            valid=True,
        )
        better_new_job = self._job(
            "Better New Shared Job",
            88,
            key="shared-job",
            valid=True,
        )

        result = merge_recommended_jobs_append_mode(
            existing_rows=[existing_row],
            all_jobs_by_key={
                "shared-job": self._job(
                    "Current Shared Job",
                    65,
                    key="shared-job",
                    date_found="2026-04-14",
                    valid=False,
                )
            },
            historical_jobs=[historical_job],
            new_jobs=[better_new_job],
            tracker_now="2026-04-14T12:00:00Z",
            tracker_today="2026-04-14",
            row_to_job=row_to_job,
            key_for_job=key_for_job,
            passes_unified_threshold=passes_unified_recommendation_threshold,
            passes_final_output_check=passes_final_output_check,
            has_manual_tracking=has_manual_tracking,
            prefers_candidate_over_existing=prefers_candidate,
        )

        self.assertEqual(result.pruned_recent_invalid_rows, 0)
        self.assertEqual(
            [job["title"] for job in result.jobs],
            ["Better New Shared Job", "Historical Valid"],
        )
        self.assertEqual(result.merged_by_key["shared-job"]["dateFound"], "2026-04-14")
        self.assertEqual(result.merged_by_key["historical-job"]["dateFound"], "2026-04-01")

    def test_append_merge_drops_rows_below_unified_threshold(self) -> None:
        row = {"outputKey": "low-score", "dateFound": "2026-04-10"}
        row_to_job = lambda row: self._job("Low Score", 19, key=row["outputKey"], date_found=row["dateFound"], valid=True)
        key_for_job = lambda job: str(job.get("outputKey") or "")

        result = merge_recommended_jobs_append_mode(
            existing_rows=[row],
            all_jobs_by_key={"low-score": self._job("Low Score", 19, key="low-score", valid=True)},
            historical_jobs=[],
            new_jobs=[],
            tracker_now="2026-04-14T12:00:00Z",
            tracker_today="2026-04-14",
            row_to_job=row_to_job,
            key_for_job=key_for_job,
            passes_unified_threshold=passes_unified_recommendation_threshold,
            passes_final_output_check=lambda job: True,
            has_manual_tracking=has_manual_tracking,
            prefers_candidate_over_existing=lambda existing, candidate: False,
        )
        self.assertEqual(result.jobs, [])


if __name__ == "__main__":
    unittest.main()

