from __future__ import annotations

import unittest

try:
    from ._helpers import create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import create_candidate, create_profile, make_temp_context  # type: ignore

from jobflow_desktop_app.search.orchestration.job_search_runner import JobSearchRunner


class JobSearchRunnerDbReadsTests(unittest.TestCase):
    def _default_runtime_config(self) -> dict:
        return {
            "search": {
                "allowPlatformListings": False,
                "platformListingDomains": ["linkedin.com"],
            },
            "filters": {
                "excludeUnavailableLinks": True,
                "excludeAggregatorLinks": True,
                "preferDirectEmployerSite": True,
            },
            "analysis": {
                "postVerifyEnabled": False,
                "postVerifyRequireChecked": True,
                "recommendScoreThreshold": 50,
            },
            "output": {
                "recommendedMode": "replace",
            },
        }

    def _seed_run(self, context, candidate_id: int) -> tuple[JobSearchRunner, int]:
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
        runner.runtime_mirror.update_configs(
            search_run_id,
            runtime_config=self._default_runtime_config(),
        )
        return runner, search_run_id

    def test_load_search_progress_prefers_sqlite_mirror(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="DB Mirror Candidate")
            create_profile(context, candidate_id, name="Hydrogen Engineer", is_active=True)
            runner, search_run_id = self._seed_run(context, candidate_id)

            runner.runtime_mirror.update_progress(
                search_run_id,
                status="running",
                stage="company_sources",
                message="Collecting jobs from company sites.",
                last_event="Processed Acme Hydrogen.",
                started_at="2026-04-16T10:00:00+00:00",
            )

            progress = runner.load_search_progress(candidate_id)
            self.assertEqual(progress.status, "running")
            self.assertEqual(progress.stage, "company_sources")
            self.assertEqual(progress.message, "Collecting jobs from company sites.")
            self.assertEqual(progress.last_event, "Processed Acme Hydrogen.")

    def test_load_results_and_stats_can_read_from_sqlite(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="DB Mirror Candidate")
            profile_id = create_profile(
                context,
                candidate_id,
                name="Hydrogen Engineer",
                scope_profile="hydrogen_core",
                is_active=True,
            )
            runner, search_run_id = self._seed_run(context, candidate_id)

            analyzed_job = {
                "title": "Hydrogen Systems Engineer",
                "company": "Acme Hydrogen",
                "location": "Berlin",
                "url": "https://acme.example/jobs/1",
                "canonicalUrl": "https://acme.example/jobs/1",
                "dateFound": "2026-04-16T10:00:00Z",
                "jd": {"applyUrl": "https://acme.example/jobs/1/apply"},
                "analysis": {
                    "overallScore": 78,
                    "fitLevelCn": "高推荐",
                    "fitTrack": "hydrogen_core",
                    "recommend": True,
                    "boundTargetRole": {
                        "profileId": profile_id,
                        "roleId": f"profile:{profile_id}",
                        "nameEn": "Hydrogen Systems Engineer",
                        "displayName": "Hydrogen Systems Engineer",
                        "targetRoleText": "Hydrogen Systems Engineer",
                        "score": 78,
                    },
                },
            }
            pending_job = {
                "title": "Battery Reliability Engineer",
                "company": "Beta Power",
                "location": "Munich",
                "url": "https://beta.example/jobs/2",
                "canonicalUrl": "https://beta.example/jobs/2",
                "dateFound": "2026-04-16T11:00:00Z",
                "analysis": {},
            }

            runner.runtime_mirror.replace_candidate_company_pool(
                candidate_id=candidate_id,
                companies=[{"name": "Acme Hydrogen", "website": "https://acme.example"}],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="found",
                jobs=[analyzed_job],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[analyzed_job, pending_job],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                jobs=[analyzed_job],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="resume_pending",
                jobs=[pending_job],
            )

            recommended_jobs = runner.load_recommended_jobs(candidate_id)
            live_jobs = runner.load_live_jobs(candidate_id)
            stats = runner.load_search_stats(candidate_id)

            self.assertEqual(len(recommended_jobs), 1)
            self.assertEqual(recommended_jobs[0].title, "Hydrogen Systems Engineer")
            self.assertEqual(len(live_jobs), 1)
            self.assertEqual(live_jobs[0].company, "Acme Hydrogen")
            self.assertEqual(stats.candidate_company_pool_count, 1)
            self.assertEqual(stats.main_discovered_job_count, 2)
            self.assertEqual(stats.main_scored_job_count, 1)
            self.assertEqual(stats.main_pending_analysis_count, 1)
            self.assertEqual(stats.displayable_result_count, 1)


if __name__ == "__main__":
    unittest.main()
