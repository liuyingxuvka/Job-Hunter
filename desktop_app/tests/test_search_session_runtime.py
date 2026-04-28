from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jobflow_desktop_app.search.orchestration import runtime_config_builder
from jobflow_desktop_app.search.orchestration.search_session_runtime import (
    SearchSessionRuntime,
    _cancelled_outcome,
    _combined_tail,
    _mark_stage_log_status,
    _refresh_python_recommended_outputs,
    _run_direct_job_discovery_stage,
    _remaining_search_session_seconds,
    _write_main_runtime_config,
)
from jobflow_desktop_app.search.stages.executor import PythonStageRunResult


class SearchSessionRuntimeTests(unittest.TestCase):
    def _make_runtime(self, run_dir: Path, *, runner=None) -> SearchSessionRuntime:
        return SearchSessionRuntime(
            runner=runner or SimpleNamespace(),
            candidate_id=7,
            candidate=SimpleNamespace(candidate_id=7),
            profiles=[],
            run_dir=run_dir,
            base_config={},
            resume_config={},
            current_main_runtime_config={},
            semantic_profile=None,
            model_override="gpt-5-nano",
            env={},
            cancel_event=None,
            write_progress=lambda **kwargs: None,
            progress_state={"current_stage": "idle"},
            max_companies=5,
            effective_max_companies=5,
            query_rotation_seed=17,
            search_session_deadline=110.0,
        )

    def test_write_main_runtime_config_updates_runtime_and_syncs_config(self) -> None:
        with TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            built_config = {
                "sources": {"maxCompaniesPerRun": 3},
                "adaptiveSearch": {},
            }
            signals = object()
            candidate_context = object()
            sync_search_run_configs = Mock()
            runner = SimpleNamespace(_sync_search_run_configs=sync_search_run_configs)
            runtime = self._make_runtime(run_dir, runner=runner)
            runtime.candidate_search_signals = signals
            runtime.candidate_context = candidate_context

            with patch.object(
                runtime_config_builder,
                "build_runtime_config",
                return_value=built_config,
            ) as build_runtime_config:
                result = _write_main_runtime_config(runtime, 99)

            self.assertEqual(result, built_config)
            self.assertEqual(runtime.current_main_runtime_config, built_config)
            self.assertEqual(runtime.effective_max_companies, 3)
            self.assertIs(
                build_runtime_config.call_args.kwargs["signals"],
                signals,
            )
            self.assertIs(
                build_runtime_config.call_args.kwargs["candidate_context"],
                candidate_context,
            )
            sync_search_run_configs.assert_called_once_with(
                runtime.search_run_id,
                runtime_config=built_config,
            )

    def test_write_main_runtime_config_refreshes_candidate_context_before_build(self) -> None:
        with TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            candidate_context = runtime_config_builder.RuntimeCandidateConfigContext(
                candidate_inputs=runtime_config_builder.RuntimeCandidateInputPrep(
                    resume_path="resume.md",
                    scope_profiles=("hydrogen_mainline",),
                    target_roles=[],
                ),
                signals=object(),
            )
            refreshed_context = object()
            runner = SimpleNamespace(
                _sync_search_run_configs=Mock(),
                runtime_mirror=SimpleNamespace(),
            )
            runtime = self._make_runtime(run_dir, runner=runner)
            runtime.candidate_context = candidate_context
            runtime.candidate_search_signals = object()

            with patch.object(
                runtime_config_builder,
                "refresh_runtime_candidate_context",
                return_value=refreshed_context,
            ) as refresh_context, patch.object(
                runtime_config_builder,
                "build_runtime_config",
                return_value={"sources": {"maxCompaniesPerRun": 5}, "adaptiveSearch": {}},
            ) as build_runtime_config:
                _write_main_runtime_config(runtime, 99)

            refresh_context.assert_called_once()
            self.assertIs(runtime.candidate_context, refreshed_context)
            self.assertIs(
                build_runtime_config.call_args.kwargs["candidate_context"],
                refreshed_context,
            )

    def test_remaining_search_session_seconds_bounds_remaining_budget(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_runtime.time.monotonic",
            return_value=100.0,
        ):
            runtime = self._make_runtime(Path(temp_dir))
            self.assertEqual(_remaining_search_session_seconds(runtime), 10)

    def test_refresh_python_recommended_outputs_calls_runner_refresh(self) -> None:
        with TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            runner = SimpleNamespace(
                _refresh_python_recommended_output_json=Mock(return_value=4),
            )
            runtime = self._make_runtime(run_dir, runner=runner)
            runtime.current_main_runtime_config = {"output": {"recommendedMode": "replace"}}

            count = _refresh_python_recommended_outputs(runtime)

            self.assertEqual(count, 4)
            runner._refresh_python_recommended_output_json.assert_called_once_with(
                run_dir,
                {"output": {"recommendedMode": "replace"}},
                search_run_id=None,
            )

    def test_stage_logging_wraps_stage_result_and_can_mark_soft_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            log_rows: dict[int, dict[str, object]] = {}

            class Mirror:
                def start_stage_log(self, **kwargs):
                    log_id = len(log_rows) + 1
                    log_rows[log_id] = {"status": "started", **kwargs}
                    return log_id

                def finish_stage_log(self, log_id, **kwargs):
                    log_rows[int(log_id)].update(kwargs)

                def update_stage_log_status(self, log_id, **kwargs):
                    log_rows[int(log_id)].update(kwargs)

            mirror = Mirror()
            runner = SimpleNamespace(runtime_mirror=mirror)
            runtime = self._make_runtime(run_dir, runner=runner)
            runtime.search_run_id = 88
            runtime.search_session_deadline = 10**12
            runtime.write_progress = Mock()

            with patch(
                "jobflow_desktop_app.search.orchestration.search_session_runtime.PythonStageExecutor.run_direct_job_discovery_stage_for_runtime",
                return_value=PythonStageRunResult(
                    success=False,
                    exit_code=1,
                    message="direct failed",
                    stdout_tail="",
                    stderr_tail="api timeout",
                    payload={"rawJobs": 3, "skippedExisting": 2},
                ),
            ):
                result = _run_direct_job_discovery_stage(
                    runtime,
                    "Searching direct jobs.",
                    "Starting direct jobs.",
                    round_number=2,
                )

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.stage_log_id, 1)
            self.assertEqual(log_rows[1]["stage_name"], "direct_job_discovery")
            self.assertEqual(log_rows[1]["round_number"], 2)
            self.assertEqual(log_rows[1]["status"], "hard_failed")
            self.assertEqual(log_rows[1]["exit_code"], 1)
            self.assertEqual(log_rows[1]["counts"]["rawJobs"], 3)

            _mark_stage_log_status(
                runtime,
                result,
                status="soft_failed",
                message="continuing with company pool",
            )

            self.assertEqual(log_rows[1]["status"], "soft_failed")
            self.assertEqual(log_rows[1]["message"], "continuing with company pool")

    def test_combined_tail_and_cancelled_outcome_preserve_labels_and_progress(self) -> None:
        with TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            write_progress = Mock()
            runner = SimpleNamespace(
                _tail=lambda text, **kwargs: str(text),
                _refresh_resume_pending_jobs=Mock(),
            )
            runtime = self._make_runtime(run_dir, runner=runner)
            runtime.write_progress = write_progress

            combined = _combined_tail(runtime, [("resume", "line a"), ("discover", "line b")])
            outcome = _cancelled_outcome(
                runtime,
                "Cancelled by user.",
                stdout_tail=combined,
                stderr_tail="",
            )

            self.assertIn("[resume]\nline a", combined)
            self.assertIn("[discover]\nline b", combined)
            self.assertTrue(outcome.cancelled)
            self.assertEqual(outcome.exit_code, -2)
            runner._refresh_resume_pending_jobs.assert_called_once_with(
                run_dir,
                current_run_id=None,
            )
            write_progress.assert_called_once()
            self.assertEqual(write_progress.call_args.kwargs["status"], "cancelled")
            self.assertEqual(write_progress.call_args.kwargs["stage"], "done")


if __name__ == "__main__":
    unittest.main()
