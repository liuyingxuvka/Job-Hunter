from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from ._helpers import OpenAISettings, create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import OpenAISettings, create_candidate, create_profile, make_temp_context  # type: ignore

from jobflow_desktop_app.search.orchestration.job_search_runner import JobSearchRunner
from jobflow_desktop_app.search.orchestration import (
    candidate_search_signals,
    job_search_runner_session,
    runtime_config_builder,
)
from jobflow_desktop_app.search.state.search_progress_state import SearchStats


class JobSearchRunnerRegressionTests(unittest.TestCase):
    def _make_runner(self, context) -> JobSearchRunner:
        return JobSearchRunner(context.paths.runtime_dir.parent)

    def test_run_search_stops_after_three_empty_discovery_rounds(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            profile_id = create_profile(context, candidate_id, name="Generalist", scope_profile="", keyword_focus="", is_active=True)
            candidate = context.candidates.get(candidate_id)
            profile = context.profiles.get(profile_id)
            self.assertIsNotNone(candidate)
            self.assertIsNotNone(profile)

            runner = self._make_runner(context)
            settings = OpenAISettings(
                api_key="test-key",
                model="gpt-5-nano",
                quality_model="gpt-5.4",
                api_key_source="direct",
                api_key_env_var="",
            )
            zero_stats = SearchStats()

            def fake_build_runtime_config(self, **kwargs):
                return {
                    "sources": {"maxCompaniesPerRun": 1},
                    "adaptiveSearch": {},
                    "companyDiscovery": {"enableAutoDiscovery": False},
                }

            with (
                patch.object(runtime_config_builder, "load_base_config", return_value={}),
                patch.object(job_search_runner_session, "load_candidate_semantic_profile_for_run", return_value=None),
                patch.object(runtime_config_builder, "build_runtime_config", side_effect=fake_build_runtime_config),
                patch.object(JobSearchRunner, "_write_resume_pending_jobs", autospec=True, return_value=0),
                patch.object(JobSearchRunner, "load_search_stats", autospec=True, return_value=zero_stats),
                patch("jobflow_desktop_app.search.orchestration.job_search_runner.time.monotonic", return_value=0.0),
            ):
                result = runner.run_search(
                    candidate,
                    [profile],
                    settings=settings,
                    max_companies=1,
                    timeout_seconds=30,
                )

            self.assertTrue(result.success)
            self.assertEqual(result.exit_code, 0)
            self.assertIn(
                "Timed search session ended after 3 consecutive full rounds with no new jobs",
                result.message,
            )

    def test_run_search_refreshes_python_recommended_output_after_successful_rounds(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            profile_id = create_profile(context, candidate_id, name="Generalist", scope_profile="", keyword_focus="", is_active=True)
            candidate = context.candidates.get(candidate_id)
            profile = context.profiles.get(profile_id)
            self.assertIsNotNone(candidate)
            self.assertIsNotNone(profile)

            runner = self._make_runner(context)
            settings = OpenAISettings(
                api_key="test-key",
                model="gpt-5-nano",
                quality_model="gpt-5.4",
                api_key_source="direct",
                api_key_env_var="",
            )
            zero_stats = SearchStats()

            def fake_build_runtime_config(self, **kwargs):
                return {
                    "sources": {"maxCompaniesPerRun": 1},
                    "adaptiveSearch": {},
                    "companyDiscovery": {"enableAutoDiscovery": False},
                    "output": {"recommendedMode": "replace"},
                }

            with (
                patch.object(runtime_config_builder, "load_base_config", return_value={}),
                patch.object(job_search_runner_session, "load_candidate_semantic_profile_for_run", return_value=None),
                patch.object(runtime_config_builder, "build_runtime_config", side_effect=fake_build_runtime_config),
                patch.object(JobSearchRunner, "_write_resume_pending_jobs", autospec=True, return_value=0),
                patch.object(JobSearchRunner, "load_search_stats", autospec=True, return_value=zero_stats),
                patch.object(JobSearchRunner, "_refresh_python_recommended_output_json", autospec=True, return_value=1) as refresh_mock,
                patch("jobflow_desktop_app.search.orchestration.job_search_runner.time.monotonic", return_value=0.0),
            ):
                result = runner.run_search(
                    candidate,
                    [profile],
                    settings=settings,
                    max_companies=1,
                    timeout_seconds=30,
                )

            self.assertTrue(result.success)
            self.assertGreaterEqual(refresh_mock.call_count, 1)

    def test_load_base_config_uses_python_runtime_defaults(self) -> None:
        first_config = runtime_config_builder.load_base_config()
        second_config = runtime_config_builder.load_base_config()

        self.assertIsInstance(first_config, dict)
        self.assertIsInstance(second_config, dict)
        self.assertIn("candidate", first_config)
        self.assertIn("search", first_config)
        self.assertIsNot(first_config, second_config)
        self.assertIsNot(first_config["candidate"], second_config["candidate"])

    def test_run_search_reuses_one_candidate_signal_payload_for_initial_main_and_resume_configs(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            profile_id = create_profile(
                context,
                candidate_id,
                name="Generalist",
                scope_profile="",
                keyword_focus="",
                is_active=True,
            )
            candidate = context.candidates.get(candidate_id)
            profile = context.profiles.get(profile_id)
            self.assertIsNotNone(candidate)
            self.assertIsNotNone(profile)

            runner = self._make_runner(context)
            settings = OpenAISettings(
                api_key="test-key",
                model="gpt-5-nano",
                quality_model="gpt-5.4",
                api_key_source="direct",
                api_key_env_var="",
            )
            shared_signals = object()
            shared_candidate_context = object()
            build_calls: list[dict[str, object]] = []

            def fake_build_runtime_config(self, **kwargs):
                build_calls.append(kwargs)
                return {
                    "sources": {"maxCompaniesPerRun": 1},
                    "adaptiveSearch": {},
                    "output": {"recommendedMode": "replace"},
                }

            with (
                patch.object(runtime_config_builder, "load_base_config", return_value={}),
                patch.object(job_search_runner_session, "load_candidate_semantic_profile_for_run", return_value=None),
                patch.object(candidate_search_signals, "collect_candidate_search_signals", return_value=shared_signals) as collect_mock,
                patch.object(
                    runtime_config_builder,
                    "build_runtime_candidate_context",
                    return_value=shared_candidate_context,
                ) as build_context_mock,
                patch.object(runtime_config_builder, "build_runtime_config", side_effect=fake_build_runtime_config),
                patch.object(JobSearchRunner, "_write_resume_pending_jobs", autospec=True, return_value=0),
                patch.object(
                    JobSearchRunner,
                    "_sync_search_run_configs",
                    autospec=True,
                    return_value=None,
                ),
                patch(
                    "jobflow_desktop_app.search.orchestration.search_session_orchestrator.run_search_session",
                    return_value=SimpleNamespace(
                        success=True,
                        exit_code=0,
                        message="ok",
                        stdout_tail="",
                        stderr_tail="",
                        cancelled=False,
                    ),
                ),
            ):
                result = runner.run_search(
                    candidate,
                    [profile],
                    settings=settings,
                    max_companies=1,
                    timeout_seconds=30,
                )

            self.assertTrue(result.success)
            self.assertEqual(collect_mock.call_count, 1)
            self.assertEqual(build_context_mock.call_count, 1)
            self.assertGreaterEqual(len(build_calls), 2)
            self.assertEqual([call["pipeline_stage"] for call in build_calls[:2]], ["main", "resume_pending"])
            self.assertEqual([call["model_override"] for call in build_calls[:2]], ["gpt-5-nano", "gpt-5-nano"])
            self.assertEqual([call["quality_model_override"] for call in build_calls[:2]], ["gpt-5.4", "gpt-5.4"])
            self.assertIs(build_calls[0]["signals"], shared_signals)
            self.assertIs(build_calls[1]["signals"], shared_signals)
            self.assertIs(build_calls[0]["candidate_context"], shared_candidate_context)
            self.assertIs(build_calls[1]["candidate_context"], shared_candidate_context)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
