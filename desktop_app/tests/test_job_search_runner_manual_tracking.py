from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.db.repositories.search_runtime import JobReviewStateRepository  # noqa: E402
from jobflow_desktop_app.search.orchestration.job_search_runner import JobSearchRunner  # noqa: E402
from jobflow_desktop_app.search.orchestration.runtime_config_builder import (  # noqa: E402
    build_company_sources_only_runtime_config,
)

try:
    from ._helpers import create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover - direct discovery fallback
    from _helpers import create_candidate, create_profile, make_temp_context  # type: ignore


class JobSearchRunnerManualTrackingTests(unittest.TestCase):
    def test_refresh_python_recommended_output_uses_db_manual_fields(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            runner = JobSearchRunner(context.paths.runtime_dir.parent)
            run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
            run_dir.mkdir(parents=True, exist_ok=True)
            search_run_id = runner.runtime_mirror.create_run(
                candidate_id=candidate_id,
                run_dir=run_dir,
                status="success",
                current_stage="done",
                started_at="2026-04-16T10:00:00+00:00",
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[
                    {
                        "url": "https://example.com/jobs/a",
                        "canonicalUrl": "https://example.com/jobs/a",
                        "title": "Fuel Cell Reliability Engineer",
                        "company": "Acme Energy",
                        "location": "Berlin, Germany",
                        "analysis": {
                            "overallScore": 76,
                            "matchScore": 76,
                            "fitLevelCn": "匹配",
                            "recommend": True,
                            "isJobPosting": True,
                            "location": "Berlin, Germany",
                            "jobPostingEvidenceCn": "岗位页",
                            "recommendReasonCn": "对口",
                            "fitTrack": "hydrogen_core",
                            "postVerify": {
                                "isValidJobPage": True,
                                "recommend": True,
                                "location": "Berlin, Germany",
                                "finalUrl": "https://example.com/jobs/a/apply",
                            },
                        },
                        "jd": {
                            "ok": True,
                            "status": 200,
                            "finalUrl": "https://example.com/jobs/a",
                            "applyUrl": "https://example.com/jobs/a/apply",
                            "rawText": "Responsibilities Qualifications Apply now",
                        },
                    }
                ],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                jobs=[],
            )
            JobReviewStateRepository(context.database).merge_manual_fields_from_jobs(
                candidate_id=candidate_id,
                jobs=[
                    {
                        "url": "https://example.com/jobs/a",
                        "canonicalUrl": "https://example.com/jobs/a",
                        "title": "Fuel Cell Reliability Engineer",
                        "company": "Acme Energy",
                        "location": "Berlin, Germany",
                        "interest": "感兴趣",
                        "appliedDate": "2026-04-15",
                        "appliedCn": "已投递",
                        "responseStatus": "已回复",
                        "notesCn": "重点关注",
                    }
                ],
            )

            with patch(
                "jobflow_desktop_app.search.orchestration.job_search_runner_runtime_io.fetch_job_details",
                return_value={
                    "ok": True,
                    "status": 200,
                    "finalUrl": "https://example.com/jobs/a/apply",
                    "redirected": False,
                    "rawText": "Responsibilities Qualifications Apply now",
                    "applyUrl": "https://example.com/jobs/a/apply",
                    "fetchedAt": "2026-04-16T10:00:00+00:00",
                    "extracted": {},
                },
            ):
                count = runner._refresh_python_recommended_output_json(
                    run_dir,
                    {
                        "output": {"recommendedMode": "replace"},
                        "analysis": {"postVerifyEnabled": True, "postVerifyRequireChecked": True},
                    },
                )

            self.assertEqual(count, 1)
            payload = runner.runtime_mirror.load_latest_bucket_jobs(
                candidate_id=candidate_id,
                job_bucket="recommended",
            )
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0].get("interest"), "感兴趣")
            self.assertEqual(payload[0].get("appliedDate"), "2026-04-15")
            self.assertTrue((run_dir / "jobs_recommended.xlsx").exists())

    def test_build_company_sources_only_runtime_config_disables_auto_discovery(self) -> None:
        runtime_config = {
            "sources": {},
            "companyDiscovery": {
                "enableAutoDiscovery": True,
                "queries": ["hydrogen companies"],
            },
        }

        db_runtime_config = build_company_sources_only_runtime_config(runtime_config)

        self.assertTrue(runtime_config["companyDiscovery"]["enableAutoDiscovery"])
        self.assertFalse(db_runtime_config["companyDiscovery"]["enableAutoDiscovery"])
        self.assertEqual(db_runtime_config["sources"], {})


if __name__ == "__main__":
    unittest.main()
