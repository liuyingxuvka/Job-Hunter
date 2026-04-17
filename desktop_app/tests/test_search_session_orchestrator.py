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
    _measure_round_progress,
    _next_empty_round_count,
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
            session_pass_timeout_seconds=30,
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
            runtime.empty_rounds_before_end = 1
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
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.runner.runtime_mirror.load_candidate_company_pool.return_value = [
                {"name": "Stable Co", "snapshotComplete": True, "snapshotPendingAnalysisCount": 0},
                {"name": "Priority Pending Co", "snapshotComplete": True, "snapshotPendingAnalysisCount": 2},
            ]
            runtime.empty_rounds_before_end = 1
            runtime.search_session_deadline = 10**12
            runtime.effective_max_companies = 1

            outcome = run_search_session(runtime)

            self.assertTrue(outcome.success)
            runtime.runner.runtime_mirror.load_candidate_company_pool.assert_called_once_with(
                candidate_id=runtime.candidate_id,
            )
            self.assertEqual(
                run_sources_stage.call_args.kwargs["selected_companies"],
                [{"name": "Priority Pending Co", "snapshotComplete": True, "snapshotPendingAnalysisCount": 2}],
            )

    def test_round_progress_helper_resets_empty_rounds_on_any_progress(self) -> None:
        before = SimpleNamespace(
            candidate_company_pool_count=2,
            main_discovered_job_count=5,
            main_pending_analysis_count=4,
            recommended_job_count=1,
        )
        after = SimpleNamespace(
            candidate_company_pool_count=3,
            main_discovered_job_count=5,
            main_pending_analysis_count=4,
            recommended_job_count=1,
        )
        progress = _measure_round_progress(before, after)
        self.assertEqual(
            progress,
            RoundProgress(
                company_pool_growth=1,
                job_growth=0,
                pending_drop=0,
                recommended_job_growth=0,
            ),
        )
        self.assertTrue(progress.made_progress)
        self.assertEqual(_next_empty_round_count(2, progress), 0)
        self.assertEqual(
            _next_empty_round_count(
                2,
                RoundProgress(
                    company_pool_growth=0,
                    job_growth=0,
                    pending_drop=0,
                    recommended_job_growth=0,
                ),
            ),
            3,
        )

    def test_round_progress_treats_recommended_job_growth_as_progress(self) -> None:
        before = SimpleNamespace(
            candidate_company_pool_count=1,
            main_discovered_job_count=2,
            main_pending_analysis_count=1,
            recommended_job_count=0,
        )
        after = SimpleNamespace(
            candidate_company_pool_count=1,
            main_discovered_job_count=2,
            main_pending_analysis_count=1,
            recommended_job_count=2,
        )

        progress = _measure_round_progress(before, after)

        self.assertEqual(
            progress,
            RoundProgress(
                company_pool_growth=0,
                job_growth=0,
                pending_drop=0,
                recommended_job_growth=2,
            ),
        )
        self.assertTrue(progress.made_progress)


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
                    job_growth=2,
                    pending_drop=2,
                    recommended_job_growth=1,
                ),
            )

    def test_run_search_session_does_not_stop_on_empty_round_before_query_discovery_attempt(self) -> None:
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
            side_effect=[
                PythonStageRunResult(success=True, exit_code=0, message="discovery r1", stdout_tail="", stderr_tail=""),
                PythonStageRunResult(success=True, exit_code=0, message="discovery r2", stdout_tail="", stderr_tail=""),
            ],
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
            runtime.empty_rounds_before_end = 1
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
            stats_round_2_before = SimpleNamespace(
                candidate_company_pool_count=1,
                main_discovered_job_count=0,
                main_pending_analysis_count=0,
                recommended_job_count=0,
            )
            stats_round_2_after = SimpleNamespace(
                candidate_company_pool_count=1,
                main_discovered_job_count=0,
                main_pending_analysis_count=0,
                recommended_job_count=0,
            )
            runtime.runner.load_search_stats.side_effect = [
                stats_round_1_before,
                stats_round_1_after,
                stats_round_2_before,
                stats_round_2_after,
            ]

            outcome = run_search_session(runtime)

            self.assertTrue(outcome.success)
            self.assertEqual(run_company_discovery_stage.call_count, 2)
            self.assertEqual(run_company_discovery_stage.call_args_list[0].kwargs["query_budget"], 0)
            self.assertEqual(run_company_discovery_stage.call_args_list[1].kwargs["query_budget"], 2)

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
            side_effect=[{"adaptiveSearch": {"discoveryBreadth": 2}, "output": {"recommendedMode": "replace"}}, RuntimeError("bad refresh")],
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
                pending_after_round=0,
                round_progress=RoundProgress(
                    company_pool_growth=0,
                    job_growth=0,
                    pending_drop=0,
                    recommended_job_growth=0,
                ),
                attempted_query_discovery=False,
            ),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_orchestrator._refresh_python_recommended_outputs",
            return_value=0,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            runtime.search_session_deadline = 10**12
            runtime.empty_rounds_before_end = 9
            runtime.current_main_runtime_config = {
                "adaptiveSearch": {"discoveryBreadth": 2},
                "output": {"recommendedMode": "replace"},
            }

            outcome = run_search_session(runtime)

            self.assertFalse(outcome.success)
            self.assertIn("could not be prepared", outcome.message)

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


if __name__ == "__main__":
    unittest.main()
