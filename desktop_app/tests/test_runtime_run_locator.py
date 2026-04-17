from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jobflow_desktop_app.db.bootstrap import initialize_database
from jobflow_desktop_app.db.connection import Database
from jobflow_desktop_app.search.state.runtime_run_locator import (
    candidate_id_from_run_dir,
    job_review_state_repository_for_run_dir,
    project_root_from_run_dir,
    runtime_db_path,
    search_run_repository_for_run_dir,
)


class RuntimeRunLocatorTests(unittest.TestCase):
    def test_candidate_id_from_run_dir_parses_expected_name(self) -> None:
        self.assertEqual(candidate_id_from_run_dir(Path("candidate_42")), 42)
        self.assertIsNone(candidate_id_from_run_dir(Path("not_a_candidate_dir")))

    def test_repositories_are_resolved_from_run_dir_when_runtime_db_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            data_dir = project_root / "runtime" / "data"
            search_runs_dir = project_root / "runtime" / "search_runs" / "candidate_7"
            data_dir.mkdir(parents=True, exist_ok=True)
            search_runs_dir.mkdir(parents=True, exist_ok=True)
            schema_path = (
                Path(__file__).resolve().parents[1]
                / "src"
                / "jobflow_desktop_app"
                / "db"
                / "schema.sql"
            )
            initialize_database(Database(runtime_db_path(project_root)), schema_path)

            self.assertEqual(project_root_from_run_dir(search_runs_dir), project_root)
            self.assertIsNotNone(search_run_repository_for_run_dir(search_runs_dir))
            self.assertIsNotNone(job_review_state_repository_for_run_dir(search_runs_dir))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
