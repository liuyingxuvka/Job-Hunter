from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.stages.executor_direct_job_stage import (  # noqa: E402
    _normalize_verified_job,
    run_direct_job_discovery_stage_db,
)


class _Mirror:
    def __init__(self) -> None:
        self.bucket_jobs = {
            "all": [
                {
                    "title": "Already Analyzed Engineer",
                    "company": "Old Co",
                    "location": "Berlin",
                    "url": "https://old.example/jobs/1",
                    "analysis": {"overallScore": 20, "recommend": False},
                }
            ],
            "found": [],
            "recommended": [],
            "resume_pending": [],
        }
        self.candidate_company_pool = [
            {
                "name": "Existing Co",
                "website": "https://existing.example",
                "cooldownUntil": "2099-01-01T00:00:00+00:00",
                "sourceWorkState": {"technicalFailureCount": 1},
                "rankingWorkState": {"technicalFailureCount": 1},
            }
        ]

    def load_run_bucket_jobs(self, *, search_run_id: int, job_bucket: str) -> list[dict]:
        del search_run_id
        return [dict(item) for item in self.bucket_jobs.get(job_bucket, [])]

    def load_candidate_bucket_jobs_merged(self, *, candidate_id: int, job_bucket: str) -> list[dict]:
        del candidate_id
        return [dict(item) for item in self.bucket_jobs.get(job_bucket, [])]

    def replace_bucket_jobs(
        self,
        *,
        search_run_id: int,
        candidate_id: int,
        job_bucket: str,
        jobs: list[dict],
    ) -> None:
        del search_run_id
        del candidate_id
        self.bucket_jobs[job_bucket] = [dict(item) for item in jobs]

    def load_candidate_company_pool(self, *, candidate_id: int) -> list[dict]:
        del candidate_id
        return [dict(item) for item in self.candidate_company_pool]

    def replace_candidate_company_pool(self, *, candidate_id: int, companies: list[dict]) -> None:
        del candidate_id
        self.candidate_company_pool = [dict(item) for item in companies]

    def refresh_counts(self, *, search_run_id: int) -> None:
        del search_run_id


class DirectJobDiscoveryStageTests(unittest.TestCase):
    def test_verified_direct_job_requires_confirmed_final_url(self) -> None:
        normalized = _normalize_verified_job(
            {
                "title": "Fuel Cell Modeling Engineer",
                "company": "Existing Co",
                "location": "Aachen",
                "url": "https://existing.example/careers",
                "finalUrl": "",
                "summary": "Model PEM fuel cell degradation and lifetime.",
                "isLiveJobPage": True,
                "hasApplyEntry": True,
            },
            config={},
        )

        self.assertEqual(normalized, {})

    def test_direct_stage_dedupes_scores_and_reactivates_company(self) -> None:
        mirror = _Mirror()
        config = {
            "candidate": {
                "semanticProfile": {"summary": "Fuel-cell degradation modeling engineer"},
                "targetRoles": [{"displayName": "Fuel Cell Modeling Engineer"}],
            },
            "search": {"model": "gpt-5-nano"},
            "analysis": {"model": "gpt-5", "lowTokenMode": True},
            "directJobDiscovery": {
                "enabled": True,
                "maxJobsPerRound": 10,
                "companyUpsertMinScore": 60,
            },
        }
        discovered_jobs = [
            {
                "title": "Already Analyzed Engineer",
                "company": "Old Co",
                "location": "Berlin",
                "url": "https://old.example/jobs/1",
                "summary": "Engineer role",
            },
            {
                "title": "Fuel Cell Modeling Engineer",
                "company": "Existing Co",
                "location": "Aachen",
                "url": "https://jobs.existing.example/jobs/fuel-cell-modeling-engineer-123",
                "summary": "Model PEM fuel cell degradation and lifetime.",
            },
        ]
        verified_jobs = [
            {
                "title": "Fuel Cell Modeling Engineer",
                "company": "Existing Co",
                "location": "Aachen",
                "url": "https://jobs.existing.example/jobs/fuel-cell-modeling-engineer-123",
                "canonicalUrl": "https://jobs.existing.example/jobs/fuel-cell-modeling-engineer-123",
                "summary": "Model PEM fuel cell degradation and lifetime.",
                "directJobVerification": {
                    "isLiveJobPage": True,
                    "hasApplyEntry": True,
                    "fastFitScore": 84,
                    "reason": "current ATS page with apply button",
                },
                "source": "direct_job_discovery",
                "sourceType": "direct_job_discovery",
            }
        ]
        verify_inputs: list[list[dict]] = []

        def fake_verify(client, *, config, jobs):
            del client
            del config
            verify_inputs.append([dict(item) for item in jobs])
            return verified_jobs

        def fake_enrich(job, *, config, client, timeout_seconds):
            del config
            del client
            del timeout_seconds
            enriched = dict(job)
            enriched["jd"] = {
                "ok": True,
                "status": 200,
                "finalUrl": enriched["url"],
                "applyUrl": f"{enriched['url']}/apply",
                "rawText": "Responsibilities include PEM fuel cell degradation modeling. Qualifications. Apply now.",
            }
            return enriched

        with (
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.discover_direct_jobs_for_candidate",
                return_value=discovered_jobs,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.verify_and_prerank_direct_jobs",
                side_effect=fake_verify,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.enrich_job_with_details",
                side_effect=fake_enrich,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.JobAnalysisService.score_job_fit",
                return_value={"overallScore": 82, "recommend": True, "reason": "strong fit"},
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.JobAnalysisService.evaluate_target_roles_for_job",
                return_value=None,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.JobAnalysisService.prepare_analysis_for_storage",
                side_effect=lambda analysis, role_binding, config: dict(analysis),
            ),
        ):
            result = run_direct_job_discovery_stage_db(
                runtime_mirror=mirror,
                search_run_id=700,
                candidate_id=9,
                run_dir=Path("C:/tmp/run"),
                config=config,
                client_instance=SimpleNamespace(),
                progress_callback=None,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.payload["skippedExisting"], 1)
        self.assertEqual(len(verify_inputs), 1)
        self.assertEqual(len(verify_inputs[0]), 1)
        self.assertEqual(mirror.bucket_jobs["resume_pending"], [])

        all_urls = {item["url"] for item in mirror.bucket_jobs["all"]}
        self.assertIn("https://jobs.existing.example/jobs/fuel-cell-modeling-engineer-123", all_urls)
        direct_job = next(
            item
            for item in mirror.bucket_jobs["all"]
            if item["url"] == "https://jobs.existing.example/jobs/fuel-cell-modeling-engineer-123"
        )
        self.assertEqual(direct_job["analysis"]["overallScore"], 82)
        self.assertTrue(direct_job["analysis"]["recommend"])
        self.assertTrue(direct_job["analysis"]["eligibleForOutput"])
        self.assertEqual(len(mirror.bucket_jobs["recommended"]), 1)

        company = next(item for item in mirror.candidate_company_pool if item["name"] == "Existing Co")
        self.assertNotIn("cooldownUntil", company)
        self.assertNotIn("sourceWorkState", company)
        self.assertNotIn("rankingWorkState", company)
        self.assertIn(
            "https://jobs.existing.example/jobs/fuel-cell-modeling-engineer-123",
            company["knownJobUrls"],
        )

    def test_direct_stage_reports_internal_failure_without_fake_success(self) -> None:
        mirror = _Mirror()
        config = {
            "candidate": {"semanticProfile": {"summary": "Hydrogen modeling engineer"}},
            "search": {"model": "gpt-5-nano"},
            "directJobDiscovery": {"enabled": True, "maxJobsPerRound": 10},
        }

        with patch(
            "jobflow_desktop_app.search.stages.executor_direct_job_stage.discover_direct_jobs_for_candidate",
            side_effect=RuntimeError("search unavailable"),
        ):
            result = run_direct_job_discovery_stage_db(
                runtime_mirror=mirror,
                search_run_id=700,
                candidate_id=9,
                run_dir=Path("C:/tmp/run"),
                config=config,
                client_instance=SimpleNamespace(),
                progress_callback=None,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("search unavailable", result.payload["error"])
        self.assertEqual(result.payload["rawJobs"], 0)

    def test_direct_stage_rejects_invalid_link_before_scoring(self) -> None:
        mirror = _Mirror()
        config = {
            "candidate": {
                "semanticProfile": {"summary": "Fuel-cell degradation modeling engineer"},
                "targetRoles": [{"displayName": "Fuel Cell Modeling Engineer"}],
            },
            "search": {"model": "gpt-5-nano"},
            "analysis": {"model": "gpt-5", "lowTokenMode": True},
            "directJobDiscovery": {"enabled": True, "maxJobsPerRound": 10},
        }
        discovered_jobs = [
            {
                "title": "Fuel Cell Modeling Engineer",
                "company": "Existing Co",
                "location": "Aachen",
                "url": "https://jobs.existing.example/jobs/expired-123",
                "summary": "Model PEM fuel cell degradation and lifetime.",
            },
        ]
        verified_jobs = [
            {
                **discovered_jobs[0],
                "canonicalUrl": "https://jobs.existing.example/jobs/expired-123",
                "directJobVerification": {
                    "isLiveJobPage": True,
                    "hasApplyEntry": True,
                    "fastFitScore": 84,
                    "reason": "AI thought this was current",
                },
                "source": "direct_job_discovery",
                "sourceType": "direct_job_discovery",
            }
        ]

        def fake_invalid_enrich(job, *, config, client, timeout_seconds):
            del config
            del client
            del timeout_seconds
            enriched = dict(job)
            enriched["jd"] = {
                "ok": False,
                "status": 404,
                "finalUrl": enriched["url"],
                "applyUrl": "",
                "rawText": "",
            }
            return enriched

        with (
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.discover_direct_jobs_for_candidate",
                return_value=discovered_jobs,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.verify_and_prerank_direct_jobs",
                return_value=verified_jobs,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.enrich_job_with_details",
                side_effect=fake_invalid_enrich,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.JobAnalysisService.score_job_fit",
            ) as score_job_fit,
        ):
            result = run_direct_job_discovery_stage_db(
                runtime_mirror=mirror,
                search_run_id=701,
                candidate_id=9,
                run_dir=Path("C:/tmp/run"),
                config=config,
                client_instance=SimpleNamespace(),
                progress_callback=None,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.payload["rejectedJobs"], 1)
        self.assertEqual(result.payload["scoredJobs"], 0)
        self.assertEqual(result.payload["recommendedJobs"], 0)
        score_job_fit.assert_not_called()
        rejected_job = next(
            item
            for item in mirror.bucket_jobs["all"]
            if item["url"] == "https://jobs.existing.example/jobs/expired-123"
        )
        self.assertTrue(rejected_job["analysis"]["prefilterRejected"])
        self.assertEqual(mirror.bucket_jobs["recommended"], [])

    def test_direct_stage_post_verifies_reachable_dynamic_page_before_output(self) -> None:
        mirror = _Mirror()
        config = {
            "candidate": {
                "semanticProfile": {"summary": "Fuel-cell degradation modeling engineer"},
                "targetRoles": [{"displayName": "Fuel Cell Modeling Engineer"}],
            },
            "search": {"model": "gpt-5-nano"},
            "analysis": {"model": "gpt-5", "lowTokenMode": True, "postVerifyEnabled": True},
            "directJobDiscovery": {"enabled": True, "maxJobsPerRound": 10},
        }
        discovered_jobs = [
            {
                "title": "Fuel Cell Modeling Engineer",
                "company": "Existing Co",
                "location": "Aachen",
                "url": "https://jobs.existing.example/jobs/dynamic-123",
                "summary": "Model PEM fuel cell degradation and lifetime.",
            },
        ]
        verified_jobs = [
            {
                **discovered_jobs[0],
                "canonicalUrl": "https://jobs.existing.example/jobs/dynamic-123",
                "directJobVerification": {
                    "isLiveJobPage": True,
                    "hasApplyEntry": True,
                    "fastFitScore": 84,
                    "reason": "current dynamic page",
                },
                "source": "direct_job_discovery",
                "sourceType": "direct_job_discovery",
            }
        ]

        def fake_reachable_enrich(job, *, config, client, timeout_seconds):
            del config
            del client
            del timeout_seconds
            enriched = dict(job)
            enriched["jd"] = {
                "ok": False,
                "status": 200,
                "finalUrl": enriched["url"],
                "applyUrl": "",
                "rawText": "",
            }
            return enriched

        with (
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.discover_direct_jobs_for_candidate",
                return_value=discovered_jobs,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.verify_and_prerank_direct_jobs",
                return_value=verified_jobs,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.enrich_job_with_details",
                side_effect=fake_reachable_enrich,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.JobAnalysisService.score_job_fit",
                return_value={"overallScore": 82, "recommend": True, "reason": "strong fit"},
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.JobAnalysisService.evaluate_target_roles_for_job",
                return_value=None,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.JobAnalysisService.prepare_analysis_for_storage",
                side_effect=lambda analysis, role_binding, config: dict(analysis),
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_direct_job_stage.JobAnalysisService.post_verify_recommended_job",
                return_value={
                    "isValidJobPage": True,
                    "recommend": True,
                    "location": "Aachen",
                    "finalUrl": "https://jobs.existing.example/jobs/dynamic-123",
                },
            ) as post_verify,
        ):
            result = run_direct_job_discovery_stage_db(
                runtime_mirror=mirror,
                search_run_id=702,
                candidate_id=9,
                run_dir=Path("C:/tmp/run"),
                config=config,
                client_instance=SimpleNamespace(),
                progress_callback=None,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.payload["postVerifyJobs"], 1)
        post_verify.assert_called_once()
        [recommended_job] = mirror.bucket_jobs["recommended"]
        self.assertTrue(recommended_job["analysis"]["eligibleForOutput"])
        self.assertFalse(recommended_job["analysis"]["postVerifySkipped"])


if __name__ == "__main__":
    unittest.main()
