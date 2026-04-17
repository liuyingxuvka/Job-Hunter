from __future__ import annotations

import unittest
from pathlib import Path

from jobflow_desktop_app.search import run_state


class RunStateTests(unittest.TestCase):
    def test_collect_resume_pending_jobs_filters_completed_and_merges_by_url(self) -> None:
        pending_a = {
            "title": "Pending A",
            "company": "Acme Robotics",
            "url": "https://example.com/jobs/a",
            "dateFound": "2026-04-14T12:00:00Z",
            "analysis": {},
        }
        pending_a_detail = {
            "title": "Pending A",
            "company": "Acme Robotics",
            "url": "https://example.com/jobs/a",
            "location": "Berlin",
            "dateFound": "2026-04-14T12:00:00Z",
            "analysis": {},
        }
        completed_b = {
            "title": "Completed B",
            "company": "Beta Systems",
            "url": "https://example.com/jobs/b",
            "dateFound": "2026-04-14T12:01:00Z",
            "analysis": {"overallScore": 80},
        }

        pending = run_state.collect_resume_pending_jobs_from_job_lists(
            [pending_a, completed_b],
            [pending_a_detail],
        )

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["url"], "https://example.com/jobs/a")
        self.assertEqual(pending[0]["location"], "Berlin")

    def test_normalize_resume_pending_jobs_keeps_only_unfinished_jobs(self) -> None:
        jobs = [
            {
                "title": "Pending A",
                "company": "Acme Robotics",
                "url": "https://example.com/jobs/a",
                "dateFound": "2026-04-14T12:00:00Z",
                "analysis": {},
            },
            {
                "title": "Completed B",
                "company": "Beta Systems",
                "url": "https://example.com/jobs/b",
                "dateFound": "2026-04-14T12:01:00Z",
                "analysis": {"recommend": False, "overallScore": 44},
            },
        ]

        normalized = run_state.normalize_resume_pending_jobs(jobs, Path("C:/tmp/run"))

        self.assertEqual([item["title"] for item in normalized], ["Pending A"])

    def test_merge_resume_pending_job_lists_merges_duplicate_entries(self) -> None:
        first = [
            {
                "title": "Pending A",
                "company": "Acme Robotics",
                "url": "https://example.com/jobs/a",
                "dateFound": "2026-04-14T12:00:00Z",
                "analysis": {},
            }
        ]
        second = [
            {
                "title": "Pending A",
                "company": "Acme Robotics",
                "url": "https://example.com/jobs/a",
                "location": "Munich",
                "dateFound": "2026-04-14T12:00:00Z",
                "analysis": {},
            }
        ]

        merged = run_state.merge_resume_pending_job_lists(Path("C:/tmp/run"), first, second)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["location"], "Munich")


if __name__ == "__main__":
    unittest.main()
