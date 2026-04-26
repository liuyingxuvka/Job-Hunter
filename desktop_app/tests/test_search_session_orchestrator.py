from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jobflow_desktop_app.search.stages.executor import PythonStageRunResult
from jobflow_desktop_app.search.orchestration.search_session_orchestrator import (
    DiscoveryRoundOutcome,
    RoundProgress,
    _run_discovery_round,
    _run_round_stage,
    run_search_session,
)
from jobflow_desktop_app.search.orchestration.search_session_resume_gate import (
    FinalizeGateResult,
    ResumeGateResult,
)
from jobflow_desktop_app.search.orchestration.search_session_runtime import (
    SearchSessionOutcome,
    SearchSessionRuntime,
    _refresh_resume_pending_jobs,
)


class SearchSessionOrchestratorTests(unittest.TestCase):
    def _make_runtime(self, run_dir: Path) -> SearchSessionRuntime:
        stats = SimpleNamespace(
            candidate_company_pool_count=0,
            main_discovered_job_count=0,
            main_pending_analysis_count=0,
            recommended_job_count=0,
        )
        runtime_mirror = SimpleNamespace(
            load_candidate_company_pool=Mock(return_value=[]),
            load_run_bucket_jobs=Mock(return_value=[]),
            replace_candidate_company_pool=Mock(),
        )
        runner = SimpleNamespace(
            runtime_mirror=runtime_mirror,
            load_search_stats=Mock(return_value=stats),
            _clear_resume_pending_jobs=Mock(),
            _refresh_resume_pending_jobs=Mock(return_value=0),
            _ensure_dict=lambda payload, key: payload.get(key, {}) if isinstance(payload, dict) else {},
            _tail=lambda text, **kwargs: str(text),
        )
        return SearchSessionRuntime(
            runner=runner,
            candidate_id=7,
            candidate=SimpleNamespace(candidate_id=7),
            profiles=[],
            run_dir=run_dir,
            base_config={},
            resume_config={"output": {"recommendedMode": "merge"}},
            current_main_runtime_config={"output": {"recommendedMode": "replace"}},
            semantic_profile=None,
            model_override="gpt-5-nano",
            env={},
            cancel_event=threading.Event(),
            write_progress=Mock(),
            progress_state={"current_stage": "idle"},
            max_companies=5,
            effective_max_companies=5,
            query_rotation_seed=17,
            search_session_deadline=9999.0,
        )

    def test_run_search_session_records_direct_discovery_stop_after_no_new_companies(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator.run_initial_resume_gate",
            return_value=ResumeGateResult(
                pending_after_round=0,
                resume_phase_failed=False,
                early_outcome=None,
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._write_main_runtime_config",
            side_effect=lambda runtime, rotation_seed: runtime.current_main_runtime_config,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_discovery_round",
            return_value=DiscoveryRoundOutcome(
                main_result=PythonStageRunResult(
                    success=True,
                    exit_code=0,
                    message="discover ok",
                    stdout_tail="",
                    stderr_tail="",
                ),
                pending_after_round=0,
                round_progress=RoundProgress(0, 0, 0, 0, 0),
                attempted_query_discovery=True,
                session_details={
                    "stopReason": "no_qualified_new_companies",
                },
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._has_ready_companies_for_sources",
            return_value=False,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_python_recommended_outputs",
            return_value=0,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.search_session_deadline = 10**12

            outcome = run_search_session(runtime)

            self.assertTrue(outcome.success)
            self.assertEqual(
                outcome.details,
                {"stopReason": "no_qualified_new_companies"},
            )
            self.assertIn(
                "Company discovery did not produce qualified new companies in this session. Try again later.",
                outcome.message,
            )

    def test_run_search_session_ends_successfully_when_direct_discovery_finds_no_new_companies(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator.run_initial_resume_gate",
            return_value=ResumeGateResult(
                pending_after_round=0,
                resume_phase_failed=False,
                early_outcome=None,
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._write_main_runtime_config",
            side_effect=lambda runtime, rotation_seed: runtime.current_main_runtime_config,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_discovery_round",
            return_value=DiscoveryRoundOutcome(
                main_result=PythonStageRunResult(
                    success=True,
                    exit_code=0,
                    message="discover ok",
                    stdout_tail="",
                    stderr_tail="",
                ),
                pending_after_round=0,
                round_progress=RoundProgress(0, 0, 0, 0, 0),
                attempted_query_discovery=True,
                session_details={
                    "stopReason": "no_qualified_new_companies",
                },
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._has_ready_companies_for_sources",
            return_value=False,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_python_recommended_outputs",
            return_value=0,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.search_session_deadline = 10**12

            outcome = run_search_session(runtime)

            self.assertTrue(outcome.success)
            self.assertEqual(
                outcome.details,
                {"stopReason": "no_qualified_new_companies"},
            )
            self.assertIn(
                "did not produce qualified new companies",
                outcome.message.lower(),
            )

    def test_run_search_session_refreshes_outputs_once_before_resume_gate_early_stop(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator.run_initial_resume_gate",
            return_value=ResumeGateResult(
                pending_after_round=1,
                resume_phase_failed=False,
                early_outcome=SearchSessionOutcome(
                    success=False,
                    exit_code=1,
                    message="Search stopped before discovery because unfinished jobs remain queued.",
                    stdout_tail="",
                    stderr_tail="",
                ),
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_python_recommended_outputs",
            return_value=0,
        ) as refresh_outputs:
            runtime = self._make_runtime(Path(temp_dir))

            outcome = run_search_session(runtime)

            self.assertFalse(outcome.success)
            self.assertEqual(outcome.exit_code, 1)
            refresh_outputs.assert_called_once_with(
                runtime,
                runtime.resume_config,
            )

    def test_run_search_session_passes_selected_companies_directly_into_sources_stage(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator.run_initial_resume_gate",
            return_value=ResumeGateResult(
                pending_after_round=0,
                resume_phase_failed=False,
                early_outcome=None,
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._write_main_runtime_config",
            side_effect=lambda runtime, rotation_seed: runtime.current_main_runtime_config,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_discovery_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="discovery ok",
                stdout_tail="",
                stderr_tail="",
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_selection_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="selection ok",
                stdout_tail="",
                stderr_tail="",
                payload={"selectedCompanies": [{"name": "Acme Hydrogen"}]},
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_sources_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="sources ok",
                stdout_tail="",
                stderr_tail="",
                payload={"remainingSelectedCompanies": []},
            ),
        ) as run_sources_stage, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_resume_pending_jobs",
            return_value=0,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_round_outputs",
            return_value=None,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.search_session_deadline = 10**12

            outcome = run_search_session(runtime)

            self.assertTrue(outcome.success)
            self.assertEqual(
                run_sources_stage.call_args.kwargs["selected_companies"],
                [{"name": "Acme Hydrogen"}],
            )

    def test_run_search_session_falls_back_to_candidate_pool_for_sources_stage(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator.run_initial_resume_gate",
            return_value=ResumeGateResult(
                pending_after_round=0,
                resume_phase_failed=False,
                early_outcome=None,
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._write_main_runtime_config",
            side_effect=lambda runtime, rotation_seed: runtime.current_main_runtime_config,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_discovery_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="discovery ok",
                stdout_tail="",
                stderr_tail="",
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_selection_stage",
            return_value=PythonStageRunResult(
                success=False,
                exit_code=1,
                message="selection failed",
                stdout_tail="",
                stderr_tail="",
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_sources_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="sources ok",
                stdout_tail="",
                stderr_tail="",
                payload={"remainingSelectedCompanies": []},
            ),
        ) as run_sources_stage, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_resume_pending_jobs",
            return_value=0,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_round_outputs",
            return_value=None,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._has_ready_companies_for_sources",
            return_value=False,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.runner.runtime_mirror.load_candidate_company_pool.return_value = [
                {
                    "name": "Stable Co",
                    "snapshotComplete": True,
                    "snapshotPendingAnalysisCount": 0,
                    "aiCompanyFitScore": 65,
                },
                {
                    "name": "Priority Pending Co",
                    "snapshotComplete": True,
                    "snapshotPendingAnalysisCount": 2,
                    "aiCompanyFitScore": 10,
                },
            ]
            runtime.search_session_deadline = 10**12
            runtime.effective_max_companies = 1

            outcome = run_search_session(runtime)

            self.assertTrue(outcome.success)
            runtime.runner.runtime_mirror.load_candidate_company_pool.assert_any_call(
                candidate_id=runtime.candidate_id,
            )
            self.assertEqual(
                run_sources_stage.call_args.kwargs["selected_companies"],
                [
                    {
                        "name": "Priority Pending Co",
                        "snapshotComplete": True,
                        "snapshotPendingAnalysisCount": 2,
                        "aiCompanyFitScore": 10,
                    }
                ],
            )

    def test_round_progress_marks_any_positive_delta_as_progress(self) -> None:
        self.assertTrue(
            RoundProgress(
                company_pool_growth=1,
                company_ranking_drop=0,
                job_growth=0,
                pending_drop=0,
                recommended_job_growth=0,
            ).made_progress
        )
        self.assertTrue(
            RoundProgress(
                company_pool_growth=0,
                company_ranking_drop=0,
                job_growth=0,
                pending_drop=0,
                recommended_job_growth=2,
            ).made_progress
        )
        self.assertFalse(
            RoundProgress(
                company_pool_growth=0,
                company_ranking_drop=0,
                job_growth=0,
                pending_drop=0,
                recommended_job_growth=0,
            ).made_progress
        )


    def test_run_round_stage_records_tails_and_returns_cancelled_outcome(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime = self._make_runtime(Path(temp_dir))
            stage_stdout: list[tuple[str, str]] = []
            stage_stderr: list[tuple[str, str]] = []
            stage_result = SimpleNamespace(
                stdout_tail="stage stdout",
                stderr_tail="stage stderr",
                cancelled=True,
            )

            outcome = _run_round_stage(
                runtime,
                stage_result,
                stage_label="company_discovery_round_2",
                cancel_message="Search cancelled while refreshing the company pool.",
                stage_stdout=stage_stdout,
                stage_stderr=stage_stderr,
            )

            self.assertIsNotNone(outcome)
            self.assertTrue(outcome.cancelled)
            self.assertEqual(stage_stdout, [("company_discovery_round_2", "stage stdout")])
            self.assertEqual(stage_stderr, [("company_discovery_round_2", "stage stderr")])

    def test_run_discovery_round_reports_progress_and_finalize_counts(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_discovery_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="discovery ok",
                stdout_tail="",
                stderr_tail="",
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_selection_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="selection ok",
                stdout_tail="",
                stderr_tail="",
                payload={"selectedCompanies": [{"name": "Acme Hydrogen"}]},
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_sources_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="sources ok",
                stdout_tail="",
                stderr_tail="",
                payload={"remainingSelectedCompanies": []},
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_resume_pending_jobs",
            return_value=2,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator.run_finalize_resume_gate",
            return_value=FinalizeGateResult(
                pending_after_round=0,
                finalize_phase_failed=False,
                finalize_status="cleared",
                cancelled_outcome=None,
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_round_outputs",
            return_value=None,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            before = SimpleNamespace(
                candidate_company_pool_count=1,
                main_discovered_job_count=2,
                main_pending_analysis_count=3,
                recommended_job_count=0,
            )
            after = SimpleNamespace(
                candidate_company_pool_count=2,
                main_discovered_job_count=4,
                main_pending_analysis_count=1,
                recommended_job_count=1,
            )
            runtime.runner.load_search_stats.return_value = after
            stage_notes: list[str] = []
            stage_stdout: list[tuple[str, str]] = []
            stage_stderr: list[tuple[str, str]] = []

            outcome = _run_discovery_round(
                runtime,
                round_number=1,
                search_stats_before_round=before,
                unresolved_company_rankings_before_round=2,
                stage_notes=stage_notes,
                stage_stdout=stage_stdout,
                stage_stderr=stage_stderr,
            )

            self.assertIsInstance(outcome, DiscoveryRoundOutcome)
            self.assertEqual(outcome.pending_after_round, 0)
            self.assertFalse(outcome.finalize_phase_failed)
            self.assertEqual(outcome.finalize_status, "cleared")
            self.assertEqual(
                outcome.round_progress,
                RoundProgress(
                    company_pool_growth=1,
                    company_ranking_drop=2,
                    job_growth=2,
                    pending_drop=2,
                    recommended_job_growth=1,
                ),
            )

    def test_run_discovery_round_runs_direct_jobs_before_company_pool(self) -> None:
        events: list[str] = []

        def direct_stage(*args, **kwargs):
            del args
            del kwargs
            events.append("direct")
            return PythonStageRunResult(
                success=True,
                exit_code=0,
                message="direct ok",
                stdout_tail="direct stdout",
                stderr_tail="",
                payload={"scoredJobs": 1, "upsertedCompanies": 1},
            )

        def company_discovery_stage(*args, **kwargs):
            del args
            del kwargs
            events.append("company_discovery")
            return PythonStageRunResult(
                success=True,
                exit_code=0,
                message="company discovery ok",
                stdout_tail="",
                stderr_tail="",
            )

        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_direct_job_discovery_stage",
            side_effect=direct_stage,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_discovery_stage",
            side_effect=company_discovery_stage,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_selection_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="selection ok",
                stdout_tail="",
                stderr_tail="",
                payload={"selectedCompanies": []},
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_resume_pending_jobs",
            return_value=0,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_round_outputs",
            return_value=None,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.search_run_id = 777
            before = SimpleNamespace(
                candidate_company_pool_count=0,
                main_discovered_job_count=0,
                main_pending_analysis_count=0,
                recommended_job_count=0,
            )
            after_direct = SimpleNamespace(
                candidate_company_pool_count=1,
                main_discovered_job_count=1,
                main_pending_analysis_count=0,
                recommended_job_count=1,
            )
            runtime.runner.load_search_stats.side_effect = [after_direct, after_direct]
            stage_notes: list[str] = []
            stage_stdout: list[tuple[str, str]] = []
            stage_stderr: list[tuple[str, str]] = []

            outcome = _run_discovery_round(
                runtime,
                round_number=1,
                search_stats_before_round=before,
                unresolved_company_rankings_before_round=0,
                stage_notes=stage_notes,
                stage_stdout=stage_stdout,
                stage_stderr=stage_stderr,
            )

            self.assertTrue(outcome.main_result.success)
            self.assertEqual(events[:2], ["direct", "company_discovery"])
            self.assertEqual(outcome.round_progress.job_growth, 1)
            self.assertEqual(outcome.round_progress.recommended_job_growth, 1)

    def test_run_discovery_round_defers_discovery_and_sources_while_company_ranking_pending(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_discovery_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="discovery ok",
                stdout_tail="",
                stderr_tail="",
            ),
        ) as run_company_discovery_stage, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_selection_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="selection ok",
                stdout_tail="",
                stderr_tail="",
                payload={"selectedCompanies": []},
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_sources_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="sources ok",
                stdout_tail="",
                stderr_tail="",
            ),
        ) as run_company_sources_stage, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_resume_pending_jobs",
            return_value=0,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_round_outputs",
            return_value=None,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.runner.runtime_mirror.load_candidate_company_pool.return_value = [
                {
                    "name": "Specialist Co",
                    "website": "https://specialist.example",
                }
            ]
            before = SimpleNamespace(
                candidate_company_pool_count=3,
                main_discovered_job_count=0,
                main_pending_analysis_count=0,
                recommended_job_count=0,
            )
            after = SimpleNamespace(
                candidate_company_pool_count=3,
                main_discovered_job_count=0,
                main_pending_analysis_count=0,
                recommended_job_count=0,
            )
            runtime.runner.load_search_stats.return_value = after
            stage_notes: list[str] = []
            stage_stdout: list[tuple[str, str]] = []
            stage_stderr: list[tuple[str, str]] = []

            outcome = _run_discovery_round(
                runtime,
                round_number=2,
                search_stats_before_round=before,
                unresolved_company_rankings_before_round=1,
                stage_notes=stage_notes,
                stage_stdout=stage_stdout,
                stage_stderr=stage_stderr,
            )

            self.assertTrue(outcome.main_result.success)
            self.assertFalse(outcome.attempted_query_discovery)
            run_company_discovery_stage.assert_not_called()
            run_company_sources_stage.assert_not_called()
            self.assertIn(
                "Python company discovery deferred until company fit ranking finishes for the current pool.",
                stage_notes,
            )

    def test_run_discovery_round_skips_discovery_when_scored_companies_are_ready_for_sources(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_discovery_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="discovery ok",
                stdout_tail="",
                stderr_tail="",
            ),
        ) as run_company_discovery_stage, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_selection_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="selection ok",
                stdout_tail="",
                stderr_tail="",
                payload={
                    "selectedCompanies": [
                        {
                            "name": "Ready Co",
                            "website": "https://ready.example",
                            "aiCompanyFitScore": 80,
                        }
                    ]
                },
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_sources_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="sources ok",
                stdout_tail="",
                stderr_tail="",
                payload={"remainingSelectedCompanies": []},
            ),
        ) as run_company_sources_stage, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_resume_pending_jobs",
            return_value=0,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_round_outputs",
            return_value=None,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.runner.runtime_mirror.load_candidate_company_pool.return_value = [
                {
                    "name": "Ready Co",
                    "website": "https://ready.example",
                    "aiCompanyFitScore": 80,
                }
            ]
            before = SimpleNamespace(
                candidate_company_pool_count=1,
                main_discovered_job_count=0,
                main_pending_analysis_count=0,
                recommended_job_count=0,
            )
            after = SimpleNamespace(
                candidate_company_pool_count=1,
                main_discovered_job_count=0,
                main_pending_analysis_count=0,
                recommended_job_count=0,
            )
            runtime.runner.load_search_stats.return_value = after
            stage_notes: list[str] = []
            stage_stdout: list[tuple[str, str]] = []
            stage_stderr: list[tuple[str, str]] = []

            outcome = _run_discovery_round(
                runtime,
                round_number=2,
                search_stats_before_round=before,
                unresolved_company_rankings_before_round=0,
                stage_notes=stage_notes,
                stage_stdout=stage_stdout,
                stage_stderr=stage_stderr,
            )

            self.assertTrue(outcome.main_result.success)
            self.assertFalse(outcome.attempted_query_discovery)
            run_company_discovery_stage.assert_not_called()
            run_company_sources_stage.assert_called_once()
            self.assertIn(
                "Python company discovery deferred because scored companies are already ready for the sources stage.",
                stage_notes,
            )

    def test_run_search_session_stops_when_no_actionable_work_units_remain(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator.run_initial_resume_gate",
            return_value=ResumeGateResult(
                pending_after_round=0,
                resume_phase_failed=False,
                early_outcome=None,
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._write_main_runtime_config",
            side_effect=lambda runtime, rotation_seed: runtime.current_main_runtime_config,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_discovery_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="discovery r1",
                stdout_tail="",
                stderr_tail="",
            ),
        ) as run_company_discovery_stage, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_selection_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="selection ok",
                stdout_tail="",
                stderr_tail="",
                payload={"selectedCompanies": []},
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_company_sources_stage",
            return_value=PythonStageRunResult(
                success=True,
                exit_code=0,
                message="sources ok",
                stdout_tail="",
                stderr_tail="",
                payload={"remainingSelectedCompanies": []},
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_resume_pending_jobs",
            return_value=0,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_round_outputs",
            return_value=None,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.search_session_deadline = 10**12
            runtime.current_main_runtime_config = {
                "adaptiveSearch": {"discoveryBreadth": 2},
                "output": {"recommendedMode": "replace"},
            }
            stats_round_1_before = SimpleNamespace(
                candidate_company_pool_count=1,
                main_discovered_job_count=0,
                main_pending_analysis_count=0,
                recommended_job_count=0,
            )
            stats_round_1_after = SimpleNamespace(
                candidate_company_pool_count=1,
                main_discovered_job_count=0,
                main_pending_analysis_count=0,
                recommended_job_count=0,
            )
            runtime.runner.load_search_stats.side_effect = [
                stats_round_1_before,
                stats_round_1_after,
            ]

            outcome = run_search_session(runtime)

            self.assertTrue(outcome.success)
            self.assertEqual(run_company_discovery_stage.call_count, 1)
            self.assertIn(
                "Timed search session ended because no actionable work units remained for this session.",
                outcome.message,
            )

    def test_run_search_session_reports_failure_when_runtime_config_refresh_fails(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator.run_initial_resume_gate",
            return_value=ResumeGateResult(
                pending_after_round=0,
                resume_phase_failed=False,
                early_outcome=None,
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._write_main_runtime_config",
            side_effect=RuntimeError("bad refresh"),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_discovery_round",
            side_effect=[
                DiscoveryRoundOutcome(
                    main_result=PythonStageRunResult(
                        success=True,
                        exit_code=0,
                        message="round ok",
                        stdout_tail="",
                        stderr_tail="",
                    ),
                    pending_after_round=0,
                    round_progress=RoundProgress(
                        company_pool_growth=1,
                        company_ranking_drop=0,
                        job_growth=0,
                        pending_drop=0,
                        recommended_job_growth=0,
                    ),
                    attempted_query_discovery=False,
                ),
                DiscoveryRoundOutcome(
                    main_result=PythonStageRunResult(
                        success=True,
                        exit_code=0,
                        message="round ok 2",
                        stdout_tail="",
                        stderr_tail="",
                    ),
                    pending_after_round=0,
                    round_progress=RoundProgress(
                        company_pool_growth=0,
                        company_ranking_drop=0,
                        job_growth=0,
                        pending_drop=0,
                        recommended_job_growth=0,
                    ),
                    attempted_query_discovery=False,
                ),
            ],
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_python_recommended_outputs",
            return_value=0,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.search_session_deadline = 10**12
            runtime.current_main_runtime_config = {
                "adaptiveSearch": {"discoveryBreadth": 2},
                "output": {"recommendedMode": "replace"},
            }

            outcome = run_search_session(runtime)

            self.assertFalse(outcome.success)
            self.assertIn("could not be prepared", outcome.message)

    def test_round_progress_treats_company_ranking_drop_as_progress(self) -> None:
        progress = RoundProgress(
            company_pool_growth=0,
            company_ranking_drop=3,
            job_growth=0,
            pending_drop=0,
            recommended_job_growth=0,
        )

        self.assertTrue(progress.made_progress)

    def test_run_search_session_reports_timeout_before_first_round_as_failure(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator.run_initial_resume_gate",
            return_value=ResumeGateResult(
                pending_after_round=0,
                resume_phase_failed=False,
                early_outcome=None,
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_python_recommended_outputs",
            return_value=0,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.search_session_deadline = -1.0

            outcome = run_search_session(runtime)

            self.assertFalse(outcome.success)
            self.assertIn("before discovery could start", outcome.message)

    def test_run_search_session_keeps_resume_pending_queue_when_successful_session_leaves_pending_jobs(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator.run_initial_resume_gate",
            return_value=ResumeGateResult(
                pending_after_round=0,
                resume_phase_failed=False,
                early_outcome=None,
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_discovery_round",
            return_value=DiscoveryRoundOutcome(
                main_result=PythonStageRunResult(
                    success=True,
                    exit_code=0,
                    message="round ok",
                    stdout_tail="",
                    stderr_tail="",
                ),
                pending_after_round=2,
                round_progress=RoundProgress(
                    company_pool_growth=0,
                    company_ranking_drop=0,
                    job_growth=0,
                    pending_drop=0,
                    recommended_job_growth=0,
                ),
                attempted_query_discovery=True,
                finalize_phase_failed=False,
                finalize_status="incomplete",
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_python_recommended_outputs",
            return_value=0,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.search_session_deadline = 10**12

            outcome = run_search_session(runtime)

            self.assertTrue(outcome.success)
            self.assertIn("Pending jobs remain queued for a later manual session (2 job(s)).", outcome.message)
            runtime.runner._clear_resume_pending_jobs.assert_not_called()

    def test_run_search_session_continues_when_pending_remains_but_round_makes_progress(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator.run_initial_resume_gate",
            return_value=ResumeGateResult(
                pending_after_round=0,
                resume_phase_failed=False,
                early_outcome=None,
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._write_main_runtime_config",
            side_effect=lambda runtime, rotation_seed: runtime.current_main_runtime_config,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._run_discovery_round",
            side_effect=[
                DiscoveryRoundOutcome(
                    main_result=PythonStageRunResult(
                        success=True,
                        exit_code=0,
                        message="round 1 ok",
                        stdout_tail="",
                        stderr_tail="",
                    ),
                    pending_after_round=2,
                    round_progress=RoundProgress(
                        company_pool_growth=1,
                        company_ranking_drop=0,
                        job_growth=1,
                        pending_drop=0,
                        recommended_job_growth=0,
                    ),
                    attempted_query_discovery=True,
                    finalize_phase_failed=False,
                    finalize_status="incomplete",
                ),
                DiscoveryRoundOutcome(
                    main_result=PythonStageRunResult(
                        success=True,
                        exit_code=0,
                        message="round 2 ok",
                        stdout_tail="",
                        stderr_tail="",
                    ),
                    pending_after_round=0,
                    round_progress=RoundProgress(0, 0, 0, 0, 0),
                    attempted_query_discovery=True,
                    finalize_phase_failed=False,
                    finalize_status="cleared",
                ),
            ],
        ) as run_discovery_round, patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._has_ready_companies_for_sources",
            return_value=False,
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_python_recommended_outputs",
            return_value=0,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.search_session_deadline = 10**12

            outcome = run_search_session(runtime)

            self.assertTrue(outcome.success)
            self.assertEqual(run_discovery_round.call_count, 2)
            self.assertIn(
                "Pending jobs remain after this round, but the session made progress; continuing while time remains.",
                outcome.message,
            )
            runtime.runner._clear_resume_pending_jobs.assert_called_once_with(
                runtime.run_dir,
                current_run_id=None,
            )

    def test_refresh_resume_pending_jobs_reconciles_company_pending_counts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime = self._make_runtime(Path(temp_dir))
            runtime.search_run_id = 99
            runtime.current_main_runtime_config = {"adaptiveSearch": {"cooldownBaseDays": 7}}
            companies = [
                {
                    "name": "Lionbridge",
                    "website": "https://careers.lionbridge.com",
                    "snapshotJobUrls": ["https://careers.lionbridge.com/jobs/director-of-language-ai"],
                    "knownJobUrls": ["https://careers.lionbridge.com/jobs/director-of-language-ai"],
                    "snapshotPendingAnalysisCount": 28,
                    "snapshotComplete": True,
                }
            ]
            runtime.runner.runtime_mirror.load_candidate_company_pool.return_value = companies
            runtime.runner.runtime_mirror.load_run_bucket_jobs.return_value = [
                {
                    "url": "https://careers.lionbridge.com/jobs/director-of-language-ai",
                    "analysis": {"recommend": True},
                }
            ]
            runtime.runner.runtime_mirror.replace_candidate_company_pool = Mock()
            runtime.runner._write_resume_pending_jobs = Mock(return_value=0)

            count = _refresh_resume_pending_jobs(runtime)

            self.assertEqual(count, 0)
            runtime.runner.runtime_mirror.replace_candidate_company_pool.assert_called_once()
            updated_companies = runtime.runner.runtime_mirror.replace_candidate_company_pool.call_args.kwargs["companies"]
            self.assertEqual(updated_companies[0]["snapshotPendingAnalysisCount"], 1)


if __name__ == "__main__":
    unittest.main()
