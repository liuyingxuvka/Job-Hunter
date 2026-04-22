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

from jobflow_desktop_app.search.stages.executor import (  # noqa: E402
    RESUME_ANALYSIS_REQUEST_TIMEOUT_SECONDS,
    RESUME_POST_VERIFY_REQUEST_TIMEOUT_SECONDS,
    PythonStageExecutor,
)


class _Mirror:
    def __init__(self) -> None:
        self.bucket_jobs = {
            "resume_pending": [
                {
                    "title": "Broken Job",
                    "company": "Acme",
                    "url": "https://example.com/a",
                    "dateFound": "2026-04-21T10:00:00Z",
                    "analysis": {},
                },
                {
                    "title": "Healthy Job",
                    "company": "Beta",
                    "url": "https://example.com/b",
                    "dateFound": "2026-04-21T10:01:00Z",
                    "analysis": {},
                },
            ],
            "all": [],
            "recommended": [],
        }
        self.replaced: list[tuple[str, list[dict]]] = []

    def load_run_bucket_jobs(self, *, search_run_id: int, job_bucket: str) -> list[dict]:
        del search_run_id
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
        self.replaced.append((job_bucket, [dict(item) for item in jobs]))

    def load_candidate_company_pool(self, *, candidate_id: int) -> list[dict]:
        del candidate_id
        return []

    def replace_candidate_company_pool(self, *, candidate_id: int, companies: list[dict]) -> None:
        del candidate_id
        del companies


class StageExecutorResumePendingTests(unittest.TestCase):
    def test_resume_stage_suspends_failed_job_and_continues(self) -> None:
        mirror = _Mirror()
        candidate_profile = {"summary": "Localization profile", "targetRoles": []}
        config = {
            "analysis": {
                "model": "gpt-5-nano",
                "postVerifyEnabled": False,
                "scoringUseWebSearch": False,
                "lowTokenMode": True,
            }
        }
        client = SimpleNamespace()

        score_calls = {"count": 0}

        def fake_score(*args, **kwargs):
            del args
            job = kwargs["job"]
            score_calls["count"] += 1
            if job["title"] == "Broken Job":
                raise TimeoutError("detail timeout")
            return {
                "overallScore": 82,
                "recommend": True,
                "reason": "good fit",
            }

        with (
            patch(
                "jobflow_desktop_app.search.stages.executor._load_candidate_profile_payload",
                return_value=candidate_profile,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor.JobAnalysisService.score_job_fit",
                side_effect=fake_score,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor.JobAnalysisService.evaluate_target_roles_for_job",
                return_value=None,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor.JobAnalysisService.prepare_analysis_for_storage",
                side_effect=lambda analysis, role_binding, config: dict(analysis),
            ),
        ):
            result = PythonStageExecutor.run_resume_pending_stage_for_runtime(
                runtime_mirror=mirror,
                search_run_id=501,
                candidate_id=9,
                run_dir=Path("C:/tmp/run"),
                config=config,
                env=None,
                timeout_seconds=120,
                cancel_event=None,
                progress_callback=None,
                client=client,
            )

        self.assertTrue(result.success)
        self.assertIn("Suspended 1 job", result.message)
        all_jobs = mirror.bucket_jobs["all"]
        broken_job = next(item for item in all_jobs if item["title"] == "Broken Job")
        healthy_job = next(item for item in all_jobs if item["title"] == "Healthy Job")
        self.assertEqual(
            broken_job["processingState"]["technicalFailureCount"],
            1,
        )
        self.assertEqual(
            broken_job["processingState"]["suspendedRunId"],
            501,
        )
        self.assertEqual(
            healthy_job["analysis"]["overallScore"],
            82,
        )
        self.assertEqual(mirror.bucket_jobs["resume_pending"], [])

    def test_resume_stage_caps_per_call_analysis_and_post_verify_timeouts(self) -> None:
        mirror = _Mirror()
        mirror.bucket_jobs["resume_pending"] = [dict(mirror.bucket_jobs["resume_pending"][1])]
        candidate_profile = {"summary": "Localization profile", "targetRoles": []}
        config = {
            "analysis": {
                "model": "gpt-5-nano",
                "postVerifyEnabled": True,
                "postVerifyCap": 1,
                "scoringUseWebSearch": False,
                "lowTokenMode": True,
            }
        }
        client = SimpleNamespace(timeout_seconds=999)
        observed_timeouts: dict[str, list[int]] = {
            "score": [],
            "binding": [],
            "postVerify": [],
        }

        def fake_score(current_client, **kwargs):
            del kwargs
            observed_timeouts["score"].append(int(current_client.timeout_seconds))
            return {
                "overallScore": 82,
                "recommend": True,
                "reason": "good fit",
            }

        def fake_binding(current_client, **kwargs):
            del kwargs
            observed_timeouts["binding"].append(int(current_client.timeout_seconds))
            return None

        def fake_post_verify(current_client, **kwargs):
            del kwargs
            observed_timeouts["postVerify"].append(int(current_client.timeout_seconds))
            return {"location": "Remote"}

        with (
            patch(
                "jobflow_desktop_app.search.stages.executor._load_candidate_profile_payload",
                return_value=candidate_profile,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor.JobAnalysisService.score_job_fit",
                side_effect=fake_score,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor.JobAnalysisService.evaluate_target_roles_for_job",
                side_effect=fake_binding,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor.JobAnalysisService.post_verify_recommended_job",
                side_effect=fake_post_verify,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor.JobAnalysisService.prepare_analysis_for_storage",
                side_effect=lambda analysis, role_binding, config: dict(analysis),
            ),
        ):
            result = PythonStageExecutor.run_resume_pending_stage_for_runtime(
                runtime_mirror=mirror,
                search_run_id=502,
                candidate_id=9,
                run_dir=Path("C:/tmp/run"),
                config=config,
                env=None,
                timeout_seconds=300,
                cancel_event=None,
                progress_callback=None,
                client=client,
            )

        self.assertTrue(result.success)
        self.assertEqual(observed_timeouts["score"], [RESUME_ANALYSIS_REQUEST_TIMEOUT_SECONDS])
        self.assertEqual(observed_timeouts["binding"], [RESUME_ANALYSIS_REQUEST_TIMEOUT_SECONDS])
        self.assertEqual(observed_timeouts["postVerify"], [RESUME_POST_VERIFY_REQUEST_TIMEOUT_SECONDS])
        self.assertEqual(client.timeout_seconds, 999)


if __name__ == "__main__":
    unittest.main()
