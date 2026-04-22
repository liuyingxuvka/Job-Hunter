from __future__ import annotations

import unittest

from jobflow_desktop_app.db.repositories.search_runtime import SearchRunRepository
from jobflow_desktop_app.search.state.runtime_run_state import SearchRunStateStore

try:
    from ._helpers import create_candidate, make_temp_context
except ImportError:  # pragma: no cover
    from _helpers import create_candidate, make_temp_context  # type: ignore


class RuntimeRunStateTests(unittest.TestCase):
    def test_progress_and_config_round_trip_through_latest_run_state(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            search_runs = SearchRunRepository(context.database)
            run_state = SearchRunStateStore(
                search_runs=search_runs,
            )

            search_run_id = run_state.create_run(
                candidate_id=candidate_id,
                run_dir=context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}",
                status="running",
                current_stage="preparing",
                started_at="2026-04-16T10:00:00+00:00",
            )
            run_state.update_progress(
                search_run_id,
                status="running",
                stage="company_sources",
                message="Collecting ATS jobs.",
                last_event="Processed 2 companies.",
                started_at="2026-04-16T10:00:00+00:00",
            )
            run_state.update_configs(
                search_run_id,
                runtime_config={"output": {"recommendedXlsxPath": "./jobs_recommended.xlsx"}},
            )

            latest_run = run_state.latest_run(candidate_id)
            self.assertIsNotNone(latest_run)
            assert latest_run is not None
            self.assertEqual(latest_run.search_run_id, search_run_id)
            self.assertEqual(latest_run.current_stage, "company_sources")
            self.assertEqual(latest_run.last_message, "Collecting ATS jobs.")

            progress_payload = run_state.load_latest_progress_payload(candidate_id)
            self.assertIsNotNone(progress_payload)
            assert progress_payload is not None
            self.assertEqual(progress_payload["status"], "running")
            self.assertEqual(progress_payload["stage"], "company_sources")
            self.assertEqual(progress_payload["lastEvent"], "Processed 2 companies.")

            runtime_config = run_state.load_run_config(candidate_id=candidate_id)
            self.assertIn("output", runtime_config)
            self.assertEqual(
                runtime_config["output"]["recommendedXlsxPath"],
                "./jobs_recommended.xlsx",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
