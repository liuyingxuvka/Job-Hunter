from __future__ import annotations

import unittest

from jobflow_desktop_app.search.state.runtime_job_sync import (
    build_runtime_bucket_rows,
    merge_runtime_jobs,
    persist_runtime_jobs,
)


class _FakeJobsRepository:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.next_id = 1

    def upsert_job(self, item: dict) -> int:
        self.items.append(dict(item))
        current = self.next_id
        self.next_id += 1
        return current


class _FakeAnalysesRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, dict]] = []

    def upsert_analysis(self, *, job_id: int, search_profile_id: int, analysis: dict) -> None:
        self.calls.append((job_id, search_profile_id, dict(analysis)))


class RuntimeJobSyncTests(unittest.TestCase):
    def test_merge_runtime_jobs_merges_duplicate_job_payloads_by_identity(self) -> None:
        merged = merge_runtime_jobs(
            [
                [
                    {
                        "title": "Hydrogen Systems Engineer",
                        "company": "Acme",
                        "url": "https://example.com/jobs/1",
                        "analysis": {"overallScore": 82},
                    }
                ],
                [
                    {
                        "title": "Hydrogen Systems Engineer",
                        "company": "Acme",
                        "url": "https://example.com/jobs/1",
                        "analysis": {"recommend": True},
                    }
                ],
            ]
        )

        self.assertEqual(len(merged), 1)
        only_item = next(iter(merged.values()))
        self.assertEqual(only_item["analysis"]["overallScore"], 82)
        self.assertTrue(only_item["analysis"]["recommend"])

    def test_merge_runtime_jobs_prefers_canonical_url_for_duplicate_keys(self) -> None:
        merged = merge_runtime_jobs(
            [
                [
                    {
                        "title": "Hydrogen Systems Engineer",
                        "company": "Acme",
                        "url": "https://example.com/jobs/source-a?utm_source=test",
                        "canonicalUrl": "https://example.com/jobs/123",
                        "analysis": {"overallScore": 71},
                    }
                ],
                [
                    {
                        "title": "Hydrogen Systems Engineer",
                        "company": "Acme",
                        "url": "https://example.com/jobs/source-b",
                        "canonicalUrl": "https://example.com/jobs/123",
                        "analysis": {"recommend": True},
                    }
                ],
            ]
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(next(iter(merged)), "https://example.com/jobs/123")
        only_item = next(iter(merged.values()))
        self.assertEqual(only_item["analysis"]["overallScore"], 71)
        self.assertTrue(only_item["analysis"]["recommend"])

    def test_persist_runtime_jobs_only_upserts_analysis_with_bound_profile(self) -> None:
        jobs_repo = _FakeJobsRepository()
        analyses_repo = _FakeAnalysesRepository()

        job_ids = persist_runtime_jobs(
            jobs_by_key={
                "job-1": {
                    "title": "Hydrogen Systems Engineer",
                    "url": "https://example.com/jobs/1",
                    "analysis": {
                        "overallScore": 85,
                        "boundTargetRole": {"profileId": 12},
                    },
                },
                "job-2": {
                    "title": "Battery Engineer",
                    "url": "https://example.com/jobs/2",
                    "analysis": {"overallScore": 30},
                },
            },
            jobs_repo=jobs_repo,
            analyses_repo=analyses_repo,
        )

        self.assertEqual(job_ids, {"job-1": 1, "job-2": 2})
        self.assertEqual(len(analyses_repo.calls), 1)
        self.assertEqual(analyses_repo.calls[0][0], 1)
        self.assertEqual(analyses_repo.calls[0][1], 12)

    def test_build_runtime_bucket_rows_materializes_runtime_flags(self) -> None:
        rows = build_runtime_bucket_rows(
            bucket="resume_pending",
            items=[
                {
                    "title": "Hydrogen Systems Engineer",
                    "company": "Acme",
                    "location": "Berlin",
                    "url": "https://example.com/jobs/1",
                    "canonicalUrl": "https://example.com/jobs/1",
                    "dateFound": "2026-04-16T10:00:00Z",
                    "analysis": {"overallScore": 88, "recommend": True},
                }
            ],
            job_ids={"https://example.com/jobs/1": 7},
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["job_id"], 7)
        self.assertEqual(row["job_key"], "https://example.com/jobs/1")
        self.assertTrue(row["analysis_completed"])
        self.assertTrue(row["recommended"])
        self.assertTrue(row["pending_resume"])

    def test_build_runtime_bucket_rows_uses_canonical_key_for_source_aliases(self) -> None:
        rows = build_runtime_bucket_rows(
            bucket="all",
            items=[
                {
                    "title": "Hydrogen Systems Engineer",
                    "company": "Acme",
                    "url": "https://example.com/jobs/source-a",
                    "canonicalUrl": "https://example.com/jobs/123?utm_source=test",
                    "analysis": {"overallScore": 88},
                }
            ],
            job_ids={"https://example.com/jobs/123": 7},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_id"], 7)
        self.assertEqual(rows[0]["job_key"], "https://example.com/jobs/123")
        self.assertEqual(rows[0]["canonical_url"], "https://example.com/jobs/123")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
