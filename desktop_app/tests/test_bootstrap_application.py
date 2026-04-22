from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobflow_desktop_app.bootstrap import bootstrap_application
from jobflow_desktop_app.db.repositories.candidates import CandidateRecord
from jobflow_desktop_app.db.repositories.search_runtime import SearchRunRepository
from jobflow_desktop_app.paths import AppPaths


class BootstrapApplicationTests(unittest.TestCase):
    def test_bootstrap_application_recovers_stale_running_runs(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        schema_path = project_root / "src" / "jobflow_desktop_app" / "db" / "schema.sql"

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "runtime"
            data_dir = runtime_root / "data"
            exports_dir = runtime_root / "exports"
            logs_dir = runtime_root / "logs"
            for path in (runtime_root, data_dir, exports_dir, logs_dir):
                path.mkdir(parents=True, exist_ok=True)
            paths = AppPaths(
                project_root=project_root,
                runtime_dir=runtime_root,
                data_dir=data_dir,
                exports_dir=exports_dir,
                logs_dir=logs_dir,
                db_path=data_dir / "jobflow_desktop.db",
                schema_path=schema_path,
            )

            with (
                patch("jobflow_desktop_app.bootstrap.build_app_paths", return_value=paths),
                patch("jobflow_desktop_app.bootstrap.ensure_demo_candidate_seeded", autospec=True, return_value=None),
            ):
                first_context = bootstrap_application()
                candidate_id = first_context.candidates.save(
                    CandidateRecord(
                        candidate_id=None,
                        name="Bootstrap Recovery Candidate",
                        email="",
                        base_location="Berlin",
                        preferred_locations="Berlin\nRemote",
                        target_directions="Localization operations",
                        notes="",
                        active_resume_path="",
                        created_at="",
                        updated_at="",
                    )
                )
                runs = SearchRunRepository(first_context.database)
                run_id = runs.create_run(
                    candidate_id=candidate_id,
                    run_dir=str(paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"),
                    status="running",
                    current_stage="company_sources",
                    started_at="2026-04-21T10:00:00+00:00",
                )

                second_context = bootstrap_application()
                latest = SearchRunRepository(second_context.database).get(run_id)

            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.status, "cancelled")
            self.assertEqual(latest.current_stage, "done")
            self.assertIn("interrupted", latest.last_message.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
