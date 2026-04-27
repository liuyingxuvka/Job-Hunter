from __future__ import annotations

import unittest

from jobflow_desktop_app.db.repositories.search_runtime import (
    CandidateCompanyRepository,
    JobAnalysisRepository,
    JobRepository,
    SearchRunRepository,
)
from jobflow_desktop_app.db.repositories.pools import CandidateJobPoolRepository
from jobflow_desktop_app.search.state.runtime_run_artifacts import SearchRunArtifactsStore

try:
    from ._helpers import create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover
    from _helpers import create_candidate, create_profile, make_temp_context  # type: ignore


class RuntimeRunArtifactsTests(unittest.TestCase):
    def test_replace_bucket_jobs_and_load_latest_bucket_jobs_round_trip(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            profile_id = create_profile(context, candidate_id)
            search_runs = SearchRunRepository(context.database)
            artifacts = SearchRunArtifactsStore(
                search_runs=search_runs,
                candidate_companies=CandidateCompanyRepository(context.database),
                jobs=JobRepository(context.database),
                analyses=JobAnalysisRepository(context.database),
                candidate_jobs=CandidateJobPoolRepository(context.database),
            )
            search_run_id = search_runs.create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/candidate_1",
                status="running",
                current_stage="resume",
                started_at="2026-04-16T10:00:00+00:00",
            )
            job = {
                "title": "Hydrogen Systems Engineer",
                "company": "Acme Hydrogen",
                "location": "Berlin",
                "url": "https://acme.example/jobs/1",
                "canonicalUrl": "https://acme.example/jobs/1",
                "dateFound": "2026-04-16T10:00:00Z",
                "analysis": {
                    "overallScore": 81,
                    "recommend": True,
                    "boundTargetRole": {
                        "profileId": profile_id,
                        "roleId": f"profile:{profile_id}",
                    },
                },
            }

            artifacts.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="found",
                jobs=[job],
            )

            latest_jobs = artifacts.load_latest_bucket_jobs(
                candidate_id=candidate_id,
                job_bucket="found",
            )

            self.assertEqual(len(latest_jobs), 1)
            self.assertEqual(latest_jobs[0]["title"], "Hydrogen Systems Engineer")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
