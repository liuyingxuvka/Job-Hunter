from __future__ import annotations

import unittest

from jobflow_desktop_app.search.state.runtime_job_sync import (
    merge_runtime_jobs,
    persist_runtime_jobs,
    write_runtime_job_pool,
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


class _FakeCandidateJobsRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def upsert_runtime_jobs(self, **kwargs) -> None:
        self.calls.append(dict(kwargs))


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

    def test_write_runtime_job_pool_upserts_candidate_pool_once_per_job_identity(self) -> None:
        jobs_repo = _FakeJobsRepository()
        analyses_repo = _FakeAnalysesRepository()
        candidate_jobs_repo = _FakeCandidateJobsRepository()

        write_runtime_job_pool(
            search_run_id=11,
            candidate_id=3,
            job_lists=[
                [
                    {
                        "title": "Hydrogen Systems Engineer",
                        "company": "Acme",
                        "location": "Berlin",
                        "url": "https://example.com/jobs/source",
                        "canonicalUrl": "https://example.com/jobs/1",
                        "dateFound": "2026-04-16T10:00:00Z",
                    }
                ],
                [
                    {
                        "title": "Hydrogen Systems Engineer",
                        "company": "Acme",
                        "url": "https://example.com/jobs/1",
                        "canonicalUrl": "https://example.com/jobs/1",
                        "analysis": {
                            "overallScore": 88,
                            "recommend": True,
                            "boundTargetRole": {"profileId": 42},
                        },
                    }
                ],
            ],
            jobs_repo=jobs_repo,
            analyses_repo=analyses_repo,
            candidate_jobs_repo=candidate_jobs_repo,
        )

        self.assertEqual(len(jobs_repo.items), 1)
        self.assertEqual(len(candidate_jobs_repo.calls), 1)
        call = candidate_jobs_repo.calls[0]
        self.assertEqual(call["candidate_id"], 3)
        self.assertEqual(call["search_run_id"], 11)
        self.assertEqual(list(call["jobs_by_key"]), ["https://example.com/jobs/1"])
        self.assertEqual(call["job_ids"], {"https://example.com/jobs/1": 1})
        self.assertEqual(analyses_repo.calls[0][1], 42)

    def test_write_runtime_job_pool_handles_empty_candidate_pool_repository(self) -> None:
        jobs_repo = _FakeJobsRepository()
        analyses_repo = _FakeAnalysesRepository()

        write_runtime_job_pool(
            search_run_id=11,
            candidate_id=3,
            job_lists=[
                [
                    {
                        "title": "Hydrogen Systems Engineer",
                        "company": "Acme",
                        "url": "https://example.com/jobs/1",
                    }
                ]
            ],
            jobs_repo=jobs_repo,
            analyses_repo=analyses_repo,
            candidate_jobs_repo=None,
        )

        self.assertEqual(len(jobs_repo.items), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
