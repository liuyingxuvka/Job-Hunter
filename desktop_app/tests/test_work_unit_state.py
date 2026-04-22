from __future__ import annotations

import unittest

from jobflow_desktop_app.search.state.work_unit_state import (
    TECHNICAL_FAILURE_LIMIT,
    clear_work_unit_state,
    is_abandoned,
    is_suspended_for_run,
    record_technical_failure,
    suspend_for_current_run,
)


class WorkUnitStateTests(unittest.TestCase):
    def test_record_technical_failure_increments_once_per_run(self) -> None:
        state = record_technical_failure({}, run_id=101, reason="timeout")
        same_run = record_technical_failure(state, run_id=101, reason="timeout")
        next_run = record_technical_failure(state, run_id=102, reason="timeout")

        self.assertEqual(state["technicalFailureCount"], 1)
        self.assertEqual(same_run["technicalFailureCount"], 1)
        self.assertEqual(next_run["technicalFailureCount"], 2)

    def test_record_technical_failure_abandons_after_limit(self) -> None:
        state: dict[str, object] = {}
        for run_id in range(1, TECHNICAL_FAILURE_LIMIT + 1):
            state = record_technical_failure(state, run_id=run_id, reason="timeout")

        self.assertTrue(is_abandoned(state))
        self.assertEqual(state["technicalFailureCount"], TECHNICAL_FAILURE_LIMIT)

    def test_suspend_for_current_run_marks_only_session_skip(self) -> None:
        state = suspend_for_current_run({}, run_id=77, reason="budget")

        self.assertTrue(is_suspended_for_run(state, 77))
        self.assertFalse(is_abandoned(state))
        self.assertEqual(state["technicalFailureCount"], 0)

    def test_clear_work_unit_state_returns_empty_mapping(self) -> None:
        self.assertEqual(clear_work_unit_state(), {})


if __name__ == "__main__":
    unittest.main()
