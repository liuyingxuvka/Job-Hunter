from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jobflow_desktop_app.search.orchestration.search_session_resume_gate import (
    run_finalize_resume_gate,
    run_initial_resume_gate,
)
from jobflow_desktop_app.search.orchestration.search_session_runtime import SearchSessionRuntime


class SearchSessionResumeGateTests(unittest.TestCase):
    def _make_runtime(self, run_dir: Path) -> SearchSessionRuntime:
        return SearchSessionRuntime(
            runner=SimpleNamespace(
                _tail=lambda text, **kwargs: str(text),
                _refresh_resume_pending_jobs=Mock(),
            ),
            candidate_id=3,
            candidate=SimpleNamespace(candidate_id=3),
            profiles=[],
            run_dir=run_dir,
            base_config={},
            resume_config={},
            current_main_runtime_config={},
            semantic_profile=None,
            model_override="gpt-5-nano",
            env={},
            cancel_event=threading.Event(),
            write_progress=Mock(),
            progress_state={"current_stage": "idle"},
            max_companies=4,
            effective_max_companies=4,
            query_rotation_seed=11,
            search_session_deadline=9999.0,
            session_pass_timeout_seconds=30,
        )

    def test_run_initial_resume_gate_retries_once_before_stopping_when_queue_does_not_shrink(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_resume_gate._refresh_resume_pending_jobs",
            side_effect=[1, 1, 1],
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_resume_gate._run_resume_stage",
            return_value=SimpleNamespace(
                success=True,
                exit_code=0,
                message="resume ok",
                stdout_tail="resume stdout",
                stderr_tail="",
                cancelled=False,
            ),
        ):
            runtime = self._make_runtime(Path(temp_dir))
            result = run_initial_resume_gate(runtime, [], [], [])

            self.assertIsNotNone(result.early_outcome)
            self.assertEqual(result.pending_after_round, 1)
            self.assertFalse(result.resume_phase_failed)
            self.assertIn("unfinished jobs remain queued", result.early_outcome.message)
            self.assertIn("repeated passes", result.early_outcome.message)

    def test_run_initial_resume_gate_clears_queue_after_successive_resume_passes(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_resume_gate._refresh_resume_pending_jobs",
            side_effect=[2, 1, 0],
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_resume_gate._run_resume_stage",
            side_effect=[
                SimpleNamespace(success=True, exit_code=0, message="resume 1", stdout_tail="", stderr_tail="", cancelled=False),
                SimpleNamespace(success=True, exit_code=0, message="resume 2", stdout_tail="", stderr_tail="", cancelled=False),
            ],
        ):
            runtime = self._make_runtime(Path(temp_dir))
            result = run_initial_resume_gate(runtime, [], [], [])

            self.assertIsNone(result.early_outcome)
            self.assertEqual(result.pending_after_round, 0)
            self.assertFalse(result.resume_phase_failed)

    def test_run_initial_resume_gate_stops_when_queue_refresh_fails(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_resume_gate._refresh_resume_pending_jobs",
            side_effect=RuntimeError("db unavailable"),
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_resume_gate._run_resume_stage",
        ) as run_resume_stage:
            runtime = self._make_runtime(Path(temp_dir))
            result = run_initial_resume_gate(runtime, [], [], [])

            self.assertIsNotNone(result.early_outcome)
            self.assertTrue(result.resume_phase_failed)
            self.assertIn("could not be refreshed", result.early_outcome.message)
            run_resume_stage.assert_not_called()

    def test_run_finalize_resume_gate_counts_completed_round(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_resume_gate._refresh_resume_pending_jobs",
            side_effect=[0],
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_resume_gate._run_resume_stage",
            return_value=SimpleNamespace(
                success=True,
                exit_code=0,
                message="finalize ok",
                stdout_tail="",
                stderr_tail="",
                cancelled=False,
            ),
        ):
            runtime = self._make_runtime(Path(temp_dir))
            result = run_finalize_resume_gate(runtime, 1, [], [], [])

            self.assertIsNone(result.cancelled_outcome)
            self.assertEqual(result.pending_after_round, 0)
            self.assertFalse(result.finalize_phase_failed)
            self.assertEqual(result.finalize_status, "cleared")

    def test_run_finalize_resume_gate_retries_once_before_marking_incomplete(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_resume_gate._refresh_resume_pending_jobs",
            side_effect=[1, 1],
        ), patch(
            "jobflow_desktop_app.search.orchestration.search_session_resume_gate._run_resume_stage",
            return_value=SimpleNamespace(
                success=True,
                exit_code=0,
                message="finalize ok",
                stdout_tail="",
                stderr_tail="",
                cancelled=False,
            ),
        ):
            runtime = self._make_runtime(Path(temp_dir))
            result = run_finalize_resume_gate(runtime, 1, [], [], [])

            self.assertEqual(result.pending_after_round, 1)
            self.assertFalse(result.finalize_phase_failed)
            self.assertEqual(result.finalize_status, "incomplete")

    def test_run_finalize_resume_gate_returns_cancelled_outcome(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "jobflow_desktop_app.search.orchestration.search_session_resume_gate._run_resume_stage",
            return_value=SimpleNamespace(
                success=False,
                exit_code=-2,
                message="cancelled",
                stdout_tail="finalize stdout",
                stderr_tail="",
                cancelled=True,
            ),
        ):
            runtime = self._make_runtime(Path(temp_dir))
            result = run_finalize_resume_gate(runtime, 1, [], [], [])

            self.assertIsNotNone(result.cancelled_outcome)
            self.assertTrue(result.cancelled_outcome.cancelled)


if __name__ == "__main__":
    unittest.main()
