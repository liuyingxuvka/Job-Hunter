from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    from ._helpers import create_candidate, create_profile, make_temp_context, save_openai_settings
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import create_candidate, create_profile, make_temp_context, save_openai_settings  # type: ignore

from jobflow_desktop_app.search.orchestration.job_search_runner import JobSearchRunner
from jobflow_desktop_app.search.output.final_output import materialize_output_eligibility


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

    def _stamp_for_output(self, job: dict) -> dict:
        return materialize_output_eligibility(job, self._default_runtime_config())

    def test_load_search_progress_prefers_sqlite_mirror(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="DB Mirror Candidate")
            create_profile(context, candidate_id, name="Hydrogen Engineer", is_active=True)
            runner, _previous_run_id = self._seed_run(context, candidate_id)
            run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
            search_run_id = runner.runtime_mirror.create_run(
                candidate_id=candidate_id,
                run_dir=run_dir,
                status="running",
                current_stage="preparing",
                started_at="2026-04-16T10:00:00+00:00",
            )

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

            analyzed_job = self._stamp_for_output(analyzed_job)

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

    def test_load_results_accumulate_across_multiple_runs_for_same_candidate(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Accumulated Candidate")
            profile_id = create_profile(
                context,
                candidate_id,
                name="Localization Manager",
                scope_profile="localization_ops",
                is_active=True,
            )
            runner, first_run_id = self._seed_run(context, candidate_id)
            second_run_id = runner.runtime_mirror.create_run(
                candidate_id=candidate_id,
                run_dir=context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}",
                status="success",
                current_stage="done",
                started_at="2026-04-16T11:00:00+00:00",
            )
            runner.runtime_mirror.update_configs(
                second_run_id,
                runtime_config=self._default_runtime_config(),
            )

            first_job = {
                "title": "Localization Program Manager",
                "company": "Lingo Corp",
                "location": "Berlin",
                "url": "https://lingo.example/jobs/1",
                "canonicalUrl": "https://lingo.example/jobs/1",
                "dateFound": "2026-04-16T10:00:00Z",
                "jd": {"applyUrl": "https://lingo.example/jobs/1/apply"},
                "analysis": {
                    "overallScore": 74,
                    "fitLevelCn": "中推荐",
                    "fitTrack": "localization_ops",
                    "recommend": True,
                    "boundTargetRole": {
                        "profileId": profile_id,
                        "roleId": f"profile:{profile_id}",
                        "nameEn": "Localization Program Manager",
                        "displayName": "Localization Program Manager",
                        "targetRoleText": "Localization Program Manager",
                        "score": 74,
                    },
                },
            }
            second_job = {
                "title": "Senior Localization Operations Manager",
                "company": "Translate Co",
                "location": "Munich",
                "url": "https://translate.example/jobs/2",
                "canonicalUrl": "https://translate.example/jobs/2",
                "dateFound": "2026-04-16T11:00:00Z",
                "jd": {"applyUrl": "https://translate.example/jobs/2/apply"},
                "analysis": {
                    "overallScore": 82,
                    "fitLevelCn": "高推荐",
                    "fitTrack": "localization_ops",
                    "recommend": True,
                    "boundTargetRole": {
                        "profileId": profile_id,
                        "roleId": f"profile:{profile_id}",
                        "nameEn": "Localization Program Manager",
                        "displayName": "Localization Program Manager",
                        "targetRoleText": "Localization Program Manager",
                        "score": 82,
                    },
                },
            }

            first_job = self._stamp_for_output(first_job)
            second_job = self._stamp_for_output(second_job)

            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=first_run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                jobs=[first_job],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=first_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[first_job],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=second_run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                jobs=[second_job],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=second_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[second_job],
            )

            recommended_jobs = runner.load_recommended_jobs(candidate_id)
            live_jobs = runner.load_live_jobs(candidate_id)

            self.assertEqual(
                {job.title for job in recommended_jobs},
                {
                    "Localization Program Manager",
                    "Senior Localization Operations Manager",
                },
            )
            self.assertEqual(
                {job.title for job in live_jobs},
                {
                    "Localization Program Manager",
                    "Senior Localization Operations Manager",
                },
            )

    def test_load_recommended_jobs_merges_output_bucket_with_recommended_all_jobs(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Recommended Merge Candidate")
            profile_id = create_profile(
                context,
                candidate_id,
                name="Hydrogen Systems Engineer",
                scope_profile="hydrogen_core",
                is_active=True,
            )
            runner, first_run_id = self._seed_run(context, candidate_id)
            second_run_id = runner.runtime_mirror.create_run(
                candidate_id=candidate_id,
                run_dir=context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}",
                status="success",
                current_stage="done",
                started_at="2026-04-16T11:00:00+00:00",
            )
            runner.runtime_mirror.update_configs(
                second_run_id,
                runtime_config=self._default_runtime_config(),
            )

            all_only_job = {
                "title": "Hydrogen Degradation Modeling Engineer",
                "company": "Fuel Cell Co",
                "location": "Berlin",
                "url": "https://fuel.example/jobs/aging",
                "canonicalUrl": "https://fuel.example/jobs/aging",
                "dateFound": "2026-04-16T10:00:00Z",
                "jd": {"applyUrl": "https://fuel.example/jobs/aging/apply"},
                "analysis": {
                    "overallScore": 79,
                    "fitLevelCn": "高推荐",
                    "fitTrack": "hydrogen_core",
                    "recommend": True,
                    "boundTargetRole": {
                        "profileId": profile_id,
                        "roleId": f"profile:{profile_id}",
                        "nameEn": "Hydrogen Systems Engineer",
                        "displayName": "Hydrogen Systems Engineer",
                        "targetRoleText": "Hydrogen Systems Engineer",
                        "score": 79,
                    },
                },
            }
            output_bucket_job = {
                "title": "Fuel Cell Validation Engineer",
                "company": "Stack Labs",
                "location": "Munich",
                "url": "https://stack.example/jobs/validation",
                "canonicalUrl": "https://stack.example/jobs/validation",
                "dateFound": "2026-04-16T11:00:00Z",
                "jd": {"applyUrl": "https://stack.example/jobs/validation/apply"},
                "analysis": {
                    "overallScore": 84,
                    "fitLevelCn": "高推荐",
                    "fitTrack": "hydrogen_core",
                    "recommend": True,
                    "boundTargetRole": {
                        "profileId": profile_id,
                        "roleId": f"profile:{profile_id}",
                        "nameEn": "Hydrogen Systems Engineer",
                        "displayName": "Hydrogen Systems Engineer",
                        "targetRoleText": "Hydrogen Systems Engineer",
                        "score": 84,
                    },
                },
            }

            all_only_job = self._stamp_for_output(all_only_job)
            output_bucket_job = self._stamp_for_output(output_bucket_job)

            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=first_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[all_only_job],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=second_run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                jobs=[output_bucket_job],
            )

            recommended_jobs = runner.load_recommended_jobs(candidate_id)
            stats = runner.load_search_stats(candidate_id)

            self.assertEqual(
                {job.title for job in recommended_jobs},
                {
                    "Hydrogen Degradation Modeling Engineer",
                    "Fuel Cell Validation Engineer",
                },
            )
            self.assertEqual(stats.recommended_job_count, 2)
            self.assertEqual(stats.displayable_result_count, 2)

    def test_load_recommended_jobs_hides_stale_policy_pool_rows(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Stale Policy Candidate")
            profile_id = create_profile(
                context,
                candidate_id,
                name="Hydrogen Systems Engineer",
                scope_profile="hydrogen_core",
                is_active=True,
            )
            runner, search_run_id = self._seed_run(context, candidate_id)
            old_policy_config = self._default_runtime_config()
            old_policy_config["analysis"]["recommendScoreThreshold"] = 20
            stale_job = materialize_output_eligibility(
                {
                    "title": "Hydrogen Systems Technician",
                    "company": "Acme Hydrogen",
                    "location": "Berlin",
                    "url": "https://acme.example/jobs/stale",
                    "canonicalUrl": "https://acme.example/jobs/stale",
                    "dateFound": "2026-04-16T10:00:00Z",
                    "jd": {"applyUrl": "https://acme.example/jobs/stale/apply"},
                    "analysis": {
                        "overallScore": 30,
                        "fitLevelCn": "低推荐",
                        "fitTrack": "hydrogen_core",
                        "recommend": True,
                        "boundTargetRole": {
                            "profileId": profile_id,
                            "roleId": f"profile:{profile_id}",
                            "nameEn": "Hydrogen Systems Engineer",
                            "displayName": "Hydrogen Systems Engineer",
                            "targetRoleText": "Hydrogen Systems Engineer",
                            "score": 30,
                        },
                    },
                },
                old_policy_config,
            )

            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                jobs=[stale_job],
            )
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[stale_job],
            )

            recommended_jobs = runner.load_recommended_jobs(candidate_id)
            stats = runner.load_search_stats(candidate_id)

            self.assertEqual(recommended_jobs, [])
            self.assertEqual(stats.recommended_job_count, 0)
            self.assertEqual(stats.displayable_result_count, 0)

    def test_load_live_jobs_generates_and_persists_display_i18n_fields(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Display I18N Candidate")
            create_profile(context, candidate_id, name="Localization Manager", is_active=True)
            save_openai_settings(context)
            runner, search_run_id = self._seed_run(context, candidate_id)

            raw_job = {
                "title": "Lokalisierungsmanager",
                "company": "Lingo Corp",
                "location": "Berlin, Deutschland",
                "url": "https://lingo.example/jobs/1",
                "canonicalUrl": "https://lingo.example/jobs/1",
                "dateFound": "2026-04-16T10:00:00Z",
                "analysis": {
                    "overallScore": 74,
                    "fitLevelCn": "中推荐",
                    "fitTrack": "localization_ops",
                    "recommend": True,
                },
            }
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[raw_job],
            )
            runner.set_job_display_i18n_context_provider(
                lambda: (context.settings.get_effective_openai_settings(), context.settings.get_openai_base_url())
            )

            translated_payload = {
                "https://lingo.example/jobs/1": {
                    "zh": {"title": "本地化经理", "location": "德国柏林"},
                    "en": {"title": "Localization Manager", "location": "Berlin, Germany"},
                }
            }
            with patch(
                "jobflow_desktop_app.search.orchestration.job_result_i18n.OpenAIRoleRecommendationService.translate_job_display_bundle",
                return_value=translated_payload,
            ) as mocked_translate:
                live_jobs = runner.load_live_jobs(candidate_id)

            self.assertEqual(len(live_jobs), 1)
            self.assertEqual(live_jobs[0].title, "Lokalisierungsmanager")
            self.assertEqual(live_jobs[0].title_zh, "本地化经理")
            self.assertEqual(live_jobs[0].title_en, "Localization Manager")
            self.assertEqual(live_jobs[0].location_zh, "德国柏林")
            self.assertEqual(live_jobs[0].location_en, "Berlin, Germany")
            mocked_translate.assert_called_once()

            persisted = runner.runtime_mirror.load_run_bucket_jobs(
                search_run_id=search_run_id,
                job_bucket="all",
            )
            self.assertEqual(
                persisted[0]["displayI18n"]["zh"]["title"],
                "本地化经理",
            )
            self.assertEqual(
                persisted[0]["displayI18n"]["en"]["location"],
                "Berlin, Germany",
            )

    def test_load_live_jobs_persists_title_i18n_when_location_is_blank(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Display I18N Blank Location")
            create_profile(context, candidate_id, name="Localization Manager", is_active=True)
            save_openai_settings(context)
            runner, search_run_id = self._seed_run(context, candidate_id)

            raw_job = {
                "title": "Lokalisierungsmanager",
                "company": "Lingo Corp",
                "location": "",
                "url": "https://lingo.example/jobs/no-location",
                "canonicalUrl": "https://lingo.example/jobs/no-location",
                "dateFound": "2026-04-16T10:00:00Z",
                "analysis": {
                    "overallScore": 74,
                    "fitLevelCn": "中推荐",
                    "fitTrack": "localization_ops",
                    "recommend": True,
                },
            }
            runner.runtime_mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[raw_job],
            )
            runner.set_job_display_i18n_context_provider(
                lambda: (context.settings.get_effective_openai_settings(), context.settings.get_openai_base_url())
            )

            translated_payload = {
                "https://lingo.example/jobs/no-location": {
                    "zh": {"title": "本地化经理", "location": ""},
                    "en": {"title": "Localization Manager", "location": ""},
                }
            }
            with patch(
                "jobflow_desktop_app.search.orchestration.job_result_i18n.OpenAIRoleRecommendationService.translate_job_display_bundle",
                return_value=translated_payload,
            ):
                live_jobs = runner.load_live_jobs(candidate_id)

            self.assertEqual(live_jobs[0].title_zh, "本地化经理")
            self.assertEqual(live_jobs[0].title_en, "Localization Manager")

            persisted = runner.runtime_mirror.load_run_bucket_jobs(
                search_run_id=search_run_id,
                job_bucket="all",
            )
            self.assertEqual(
                persisted[0]["displayI18n"]["zh"]["title"],
                "本地化经理",
            )
            self.assertEqual(
                persisted[0]["displayI18n"]["en"]["title"],
                "Localization Manager",
            )


if __name__ == "__main__":
    unittest.main()
