from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from ._helpers import create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import create_candidate, create_profile, make_temp_context  # type: ignore

from jobflow_desktop_app.ai.role_recommendations import (  # noqa: E402
    encode_bilingual_description,
    encode_bilingual_role_name,
)
from jobflow_desktop_app.db.repositories.profiles import SearchProfileRecord  # noqa: E402
from jobflow_desktop_app.search.orchestration.job_search_runner import JobSearchRunner
from jobflow_desktop_app.search.output.final_output import materialize_output_eligibility
from jobflow_desktop_app.search.runtime_strategy import derive_adaptive_runtime_strategy


class JobSearchRunnerUnitTests(unittest.TestCase):
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

    def _make_runner(self, context) -> JobSearchRunner:
        return JobSearchRunner(context.paths.runtime_dir.parent)

    def _stamp_for_output(self, job: dict) -> dict:
        return materialize_output_eligibility(job, self._default_runtime_config())

    def _run_dir(self, context, candidate_id: int) -> Path:
        run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _seed_run(self, context, candidate_id: int) -> tuple[JobSearchRunner, Path, int]:
        runner = self._make_runner(context)
        run_dir = self._run_dir(context, candidate_id)
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
        return runner, run_dir, search_run_id

    def test_derive_adaptive_runtime_strategy_matches_main_stage_defaults(self) -> None:
        strategy = derive_adaptive_runtime_strategy(
            {
                "companyBatchSize": 4,
                "discoveryBreadth": 4,
                "cooldownBaseDays": 7,
            }
        )
        self.assertEqual(strategy["max_companies_per_run"], 4)
        self.assertEqual(strategy["max_jobs_per_company"], 6)
        self.assertEqual(strategy["analysis_work_cap"], 24)
        self.assertEqual(strategy["company_rotation_interval_days"], 2)
        self.assertEqual(strategy["max_jobs_per_query"], 10)

    def test_load_recommended_jobs_reads_materialized_bucket_from_sqlite(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            runner, _, search_run_id = self._seed_run(context, candidate_id)

            low_score = {
                "title": "Low Match Role",
                "company": "Acme Robotics",
                "url": "https://example.com/jobs/low",
                "dateFound": "2026-04-14T12:00:00Z",
                "jd": {"applyUrl": "https://example.com/jobs/low/apply"},
                "analysis": {
                    "recommend": True,
                    "overallScore": 49,
                },
            }
            high_score = {
                "title": "High Match Role",
                "company": "Acme Robotics",
                "url": "https://example.com/jobs/high",
                "dateFound": "2026-04-14T12:01:00Z",
                "jd": {"applyUrl": "https://example.com/jobs/high/apply"},
                "analysis": {
                    "recommend": True,
                    "overallScore": 50,
                },
            }
            skipped = {
                "title": "Not Recommended",
                "company": "Acme Robotics",
                "url": "https://example.com/jobs/skip",
                "dateFound": "2026-04-14T12:02:00Z",
                "jd": {"applyUrl": "https://example.com/jobs/skip/apply"},
                "analysis": {
                    "recommend": False,
                    "overallScore": 100,
                },
            }
            unstamped_legacy = {
                "title": "Legacy Unstamped Recommendation",
                "company": "Acme Robotics",
                "url": "https://example.com/jobs/legacy",
                "dateFound": "2026-04-14T12:03:00Z",
                "jd": {"applyUrl": "https://example.com/jobs/legacy/apply"},
                "analysis": {
                    "recommend": True,
                    "overallScore": 90,
                },
            }

            low_score = self._stamp_for_output(low_score)
            high_score = self._stamp_for_output(high_score)
            skipped = self._stamp_for_output(skipped)

            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                jobs=[low_score, high_score, unstamped_legacy],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[low_score, high_score, skipped, unstamped_legacy],
            )

            loaded = runner.load_recommended_jobs(candidate_id)
            self.assertEqual([job.title for job in loaded], ["High Match Role"])
            self.assertEqual(loaded[0].match_score, 50)
            self.assertEqual(loaded[0].overall_match_score, 50)

    def test_load_search_stats_counts_discovery_and_pending_jobs(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            runner, _, search_run_id = self._seed_run(context, candidate_id)

            found_a = {
                "title": "Found One",
                "company": "Acme Robotics",
                "url": "https://example.com/jobs/a",
                "dateFound": "2026-04-14T12:00:00Z",
                "jd": {"applyUrl": "https://example.com/jobs/a/apply"},
                "analysis": {
                    "recommend": True,
                    "overallScore": 60,
                },
            }
            found_b = {
                "title": "Found Two",
                "company": "Beta Systems",
                "url": "https://example.com/jobs/b",
                "dateFound": "2026-04-14T12:01:00Z",
                "analysis": {"overallScore": 45, "matchScore": 45, "recommend": False},
            }
            pending = {
                "title": "Pending Review",
                "company": "Gamma Manufacturing",
                "url": "https://example.com/jobs/pending",
                "dateFound": "2026-04-14T12:02:00Z",
                "analysis": {},
            }
            low_recommended = {
                "title": "Low Recommendation",
                "company": "Gamma Manufacturing",
                "url": "https://example.com/jobs/d",
                "dateFound": "2026-04-14T12:03:00Z",
                "jd": {"applyUrl": "https://example.com/jobs/d/apply"},
                "analysis": {
                    "recommend": True,
                    "overallScore": 49,
                },
            }

            found_a = self._stamp_for_output(found_a)
            low_recommended = self._stamp_for_output(low_recommended)

            runner.runtime_mirror.replace_candidate_company_pool(
                candidate_id=candidate_id,
                companies=[
                    {"name": "Acme Robotics"},
                    {"name": "Beta Systems"},
                    {"name": "Gamma Manufacturing"},
                ],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="found",
                jobs=[found_a, found_b],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[found_a, found_b, pending],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                jobs=[found_a, low_recommended],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="resume_pending",
                jobs=[pending],
            )

            stats = runner.load_search_stats(candidate_id)
            self.assertEqual(stats.discovered_job_count, 4)
            self.assertEqual(stats.discovered_company_count, 3)
            self.assertEqual(stats.scored_job_count, 3)
            self.assertEqual(stats.recommended_job_count, 1)
            self.assertEqual(stats.displayable_result_count, 1)
            self.assertEqual(stats.pending_resume_count, 1)
            self.assertEqual(stats.main_discovered_job_count, 4)
            self.assertEqual(stats.main_scored_job_count, 3)
            self.assertEqual(stats.main_pending_analysis_count, 1)

    def test_load_search_progress_reads_runtime_state_from_db(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            runner = self._make_runner(context)
            run_dir = self._run_dir(context, candidate_id)

            started_at = (datetime.now(timezone.utc) - timedelta(hours=1, minutes=5)).replace(
                microsecond=0
            )
            started_at_text = started_at.isoformat().replace("+00:00", "Z")
            search_run_id = runner._create_search_run(
                candidate_id=candidate_id,
                run_dir=run_dir,
                status="running",
                current_stage="company-first",
                started_at=started_at_text,
            )
            runner._write_search_progress(
                run_dir,
                status="running",
                stage="company-first",
                message="Searching companies.",
                last_event="Initializing search workspace.",
                started_at=started_at_text,
                search_run_id=search_run_id,
            )

            progress = runner.load_search_progress(candidate_id)
            self.assertEqual(progress.status, "running")
            self.assertEqual(progress.stage, "company-first")
            self.assertEqual(progress.message, "Searching companies.")
            self.assertEqual(progress.last_event, "Initializing search workspace.")
            self.assertEqual(progress.started_at, started_at_text)
            self.assertTrue(progress.updated_at)
            self.assertGreaterEqual(progress.elapsed_seconds, 60 * 60)

    def test_refresh_python_recommended_output_writes_db_bucket_and_xlsx(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            runner, run_dir, search_run_id = self._seed_run(context, candidate_id)

            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[
                    {
                        "title": "Fuel Cell Reliability Engineer",
                        "company": "Acme Hydrogen",
                        "location": "Berlin, Germany",
                        "url": "https://acme.example.com/careers/jobs/12345",
                        "dateFound": "2026-04-14T12:00:00Z",
                        "summary": "Hydrogen durability diagnostics role.",
                        "sourceType": "company",
                        "jd": {
                            "applyUrl": "https://acme.example.com/careers/jobs/12345/apply",
                            "finalUrl": "https://acme.example.com/careers/jobs/12345",
                            "status": 200,
                            "ok": True,
                            "rawText": "Responsibilities Qualifications Apply now",
                        },
                        "analysis": {
                            "recommend": True,
                            "overallScore": 74,
                            "matchScore": 74,
                            "fitLevelCn": "匹配",
                            "fitTrack": "hydrogen_core",
                            "jobCluster": "Core-Domain",
                            "primaryEvidenceCn": "氢能耐久性与诊断关键词",
                            "summaryCn": "氢能耐久性岗位",
                            "recommendReasonCn": "与候选人方向相符",
                        },
                    }
                ],
            )

            count = runner._refresh_python_recommended_output_json(
                run_dir,
                {
                    "candidate": {"scopeProfile": "hydrogen_mainline"},
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
                    },
                    "output": {"recommendedMode": "replace"},
                },
            )

            self.assertEqual(count, 1)
            recommended_jobs = runner.runtime_mirror.load_latest_bucket_jobs(
                candidate_id=candidate_id,
                job_bucket="recommended",
            )
            self.assertEqual(len(recommended_jobs), 1)
            self.assertEqual(recommended_jobs[0]["company"], "Acme Hydrogen")
            self.assertTrue((run_dir / "jobs_recommended.xlsx").exists())

    def test_refresh_python_recommended_output_writes_explicit_run_not_latest(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            runner, run_dir, old_run_id = self._seed_run(context, candidate_id)
            new_run_id = runner.runtime_mirror.create_run(
                candidate_id=candidate_id,
                run_dir=run_dir,
                status="running",
                current_stage="preparing",
                started_at="2026-04-16T10:05:00+00:00",
            )
            job = {
                "title": "Fuel Cell Reliability Engineer",
                "company": "Acme Hydrogen",
                "location": "Berlin, Germany",
                "url": "https://acme.example.com/careers/jobs/12345",
                "dateFound": "2026-04-14T12:00:00Z",
                "summary": "Hydrogen durability diagnostics role.",
                "sourceType": "company",
                "jd": {
                    "applyUrl": "https://acme.example.com/careers/jobs/12345/apply",
                    "finalUrl": "https://acme.example.com/careers/jobs/12345",
                    "status": 200,
                    "ok": True,
                    "rawText": "Responsibilities Qualifications Apply now",
                },
                "analysis": {
                    "recommend": True,
                    "overallScore": 74,
                    "matchScore": 74,
                    "fitTrack": "hydrogen_core",
                },
            }
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=old_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[job],
            )

            count = runner._refresh_python_recommended_output_json(
                run_dir,
                self._default_runtime_config(),
                search_run_id=old_run_id,
            )

            self.assertEqual(count, 1)
            self.assertEqual(
                len(runner.runtime_mirror.load_run_bucket_jobs(search_run_id=old_run_id, job_bucket="recommended")),
                1,
            )
            self.assertEqual(
                runner.runtime_mirror.load_run_bucket_jobs(search_run_id=new_run_id, job_bucket="recommended"),
                [],
            )

    def test_refresh_python_recommended_output_keeps_localization_target_role_binding(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Localization Candidate",
                notes="Localization operations, glossary management, vendor coordination",
            )
            profile_id = context.profiles.save(
                SearchProfileRecord(
                    profile_id=None,
                    candidate_id=candidate_id,
                    name="Localization Project Manager",
                    scope_profile="",
                    target_role="Localization Project Manager",
                    location_preference="Berlin\nRemote Germany",
                    role_name_i18n=encode_bilingual_role_name(
                        "本地化项目经理",
                        "Localization Project Manager",
                    ),
                    keyword_focus=encode_bilingual_description(
                        "本地化运营与术语管理",
                        "Localization operations and terminology management",
                    ),
                    is_active=True,
                )
            )
            runner, run_dir, search_run_id = self._seed_run(context, candidate_id)

            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[
                    {
                        "title": "Localization Project Manager",
                        "company": "Lionbridge",
                        "location": "Berlin, Germany",
                        "url": "https://lionbridge.example/jobs/loc-pm",
                        "dateFound": "2026-04-17T12:00:00Z",
                        "summary": "Lead localization delivery, vendor operations, and TMS workflows.",
                        "sourceType": "company",
                        "jd": {
                            "applyUrl": "https://lionbridge.example/jobs/loc-pm/apply",
                            "finalUrl": "https://lionbridge.example/jobs/loc-pm",
                            "status": 200,
                            "ok": True,
                            "rawText": "Localization PM JD",
                        },
                        "analysis": {
                            "recommend": True,
                            "overallScore": 81,
                            "matchScore": 81,
                            "targetRoleScore": 84,
                            "boundTargetRole": {
                                "profileId": profile_id,
                                "nameZh": "本地化项目经理",
                                "nameEn": "Localization Project Manager",
                                "displayName": "Localization Project Manager",
                                "targetRoleText": "Localization Project Manager",
                            },
                        },
                    },
                    {
                        "title": "Senior Mechanical Engineer",
                        "company": "Lionbridge",
                        "location": "Munich, Germany",
                        "url": "https://lionbridge.example/jobs/mech-eng",
                        "dateFound": "2026-04-17T12:01:00Z",
                        "summary": "Mechanical design role.",
                        "analysis": {
                            "recommend": False,
                            "overallScore": 30,
                            "matchScore": 30,
                        },
                    },
                ],
            )

            count = runner._refresh_python_recommended_output_json(
                run_dir,
                {
                    "candidate": {"scopeProfile": ""},
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
                    "output": {"recommendedMode": "replace"},
                },
            )

            self.assertEqual(count, 1)
            recommended = runner.load_recommended_jobs(candidate_id)
            self.assertEqual([job.title for job in recommended], ["Localization Project Manager"])
            self.assertEqual(recommended[0].bound_target_role_profile_id, profile_id)
            self.assertEqual(recommended[0].bound_target_role_name_zh, "本地化项目经理")
            self.assertEqual(recommended[0].bound_target_role_name_en, "Localization Project Manager")
            stats = runner.load_search_stats(candidate_id)
            self.assertEqual(stats.recommended_job_count, 1)
            self.assertEqual(stats.displayable_result_count, 1)
            self.assertTrue((run_dir / "jobs_recommended.xlsx").exists())

    def test_write_resume_pending_jobs_seeds_current_run_from_previous_run_when_current_is_empty(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Resume Pending Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            runner, run_dir, previous_run_id = self._seed_run(context, candidate_id)

            pending_job = {
                "title": "Localization Operations Manager",
                "company": "Acme",
                "url": "https://acme.example/jobs/loc-ops",
                "dateFound": "2026-04-21T10:00:00Z",
                "analysis": {},
            }
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=previous_run_id,
                candidate_id=candidate_id,
                job_bucket="resume_pending",
                jobs=[pending_job],
            )
            current_run_id = runner.runtime_mirror.create_run(
                candidate_id=candidate_id,
                run_dir=run_dir,
                status="running",
                current_stage="preparing",
                started_at="2026-04-21T10:05:00+00:00",
            )

            count = runner._write_resume_pending_jobs(
                run_dir,
                include_found_fallback=True,
                current_run_id=current_run_id,
            )

            self.assertEqual(count, 1)
            current_pending = runner.runtime_mirror.load_run_bucket_jobs(
                search_run_id=current_run_id,
                job_bucket="resume_pending",
            )
            self.assertEqual(len(current_pending), 1)
            self.assertEqual(current_pending[0]["title"], "Localization Operations Manager")

    def test_write_resume_pending_jobs_reconciles_current_bucket_against_completed_all_jobs(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Resume Pending Reconcile Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            runner, run_dir, _previous_run_id = self._seed_run(context, candidate_id)

            current_run_id = runner.runtime_mirror.create_run(
                candidate_id=candidate_id,
                run_dir=run_dir,
                status="running",
                current_stage="resume_pending",
                started_at="2026-04-21T10:05:00+00:00",
            )
            pending_job = {
                "title": "Localization Operations Manager",
                "company": "Acme",
                "url": "https://acme.example/jobs/loc-ops",
                "dateFound": "2026-04-21T10:00:00Z",
                "analysis": {},
            }
            completed_job = {
                **pending_job,
                "analysis": {
                    "overallScore": 82,
                    "recommend": True,
                },
            }
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=current_run_id,
                candidate_id=candidate_id,
                job_bucket="resume_pending",
                jobs=[pending_job],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=current_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[completed_job],
            )

            count = runner._write_resume_pending_jobs(
                run_dir,
                include_found_fallback=True,
                current_run_id=current_run_id,
            )

            self.assertEqual(count, 0)
            current_pending = runner.runtime_mirror.load_run_bucket_jobs(
                search_run_id=current_run_id,
                job_bucket="resume_pending",
            )
            self.assertEqual(current_pending, [])

    def test_write_resume_pending_jobs_writes_explicit_run_not_latest(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Resume Pending Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            runner, run_dir, old_run_id = self._seed_run(context, candidate_id)
            new_run_id = runner.runtime_mirror.create_run(
                candidate_id=candidate_id,
                run_dir=run_dir,
                status="running",
                current_stage="preparing",
                started_at="2026-04-21T10:05:00+00:00",
            )
            pending_job = {
                "title": "Fuel Cell Validation Engineer",
                "company": "Acme",
                "url": "https://acme.example/jobs/fuel-cell-validation",
                "dateFound": "2026-04-21T10:00:00Z",
                "analysis": {},
            }
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=old_run_id,
                candidate_id=candidate_id,
                job_bucket="resume_pending",
                jobs=[pending_job],
            )

            count = runner._write_resume_pending_jobs(
                run_dir,
                include_found_fallback=True,
                current_run_id=old_run_id,
            )

            self.assertEqual(count, 1)
            self.assertEqual(
                len(runner.runtime_mirror.load_run_bucket_jobs(search_run_id=old_run_id, job_bucket="resume_pending")),
                1,
            )
            self.assertEqual(
                runner.runtime_mirror.load_run_bucket_jobs(search_run_id=new_run_id, job_bucket="resume_pending"),
                [],
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
